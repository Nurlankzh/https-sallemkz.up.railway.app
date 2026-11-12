import asyncio
import logging
import os
import signal
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

import database
import config

logger = logging.getLogger(__name__)


class RunnerManager:
    def __init__(self):
        self.processes: Dict[int, asyncio.subprocess.Process] = {}
        self.restart_counts: Dict[int, int] = {}
        self._monitor_tasks: Dict[int, asyncio.Task] = {}

    def is_running(self, bot_id: int) -> bool:
        proc = self.processes.get(bot_id)
        if proc is None:
            return False
        return proc.returncode is None

    async def get_process_info(self, bot_id: int) -> Optional[dict]:
        proc = self.processes.get(bot_id)
        if proc is None:
            return {"running": False, "pid": None}

        running = proc.returncode is None
        return {
            "running": running,
            "pid": proc.pid if running else None,
            "returncode": proc.returncode,
        }

    def _workspace_path(self, bot_id: int) -> Path:
        path = Path(config.WORKSPACE_DIR) / f"bot_{bot_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _prepare_workspace(self, bot_id: int) -> Tuple[bool, str]:
        """Write latest code + env + files into workspace."""
        code = await database.get_latest_code(bot_id)
        if not code:
            return False, "❌ Код табылмады. Алдымен код жүктеңіз."

        bot_data = await database.get_bot(bot_id)
        if not bot_data:
            return False, "❌ Бот табылмады."

        workspace = self._workspace_path(bot_id)

        # Clean old files except we keep structure
        for item in workspace.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item, ignore_errors=True)

        # Write main bot code
        main_file = workspace / "bot.py"
        main_file.write_text(code, encoding="utf-8")

        # Write environment variables as .env style + inject token
        env_vars = await database.get_env_vars(bot_id)
        env_vars["BOT_TOKEN"] = bot_data["token"]
        env_vars["TELEGRAM_BOT_TOKEN"] = bot_data["token"]

        env_file = workspace / ".env"
        with open(env_file, "w", encoding="utf-8") as f:
            for k, v in env_vars.items():
                f.write(f"{k}={v}\n")

        # Write extra files
        files = await database.get_bot_files(bot_id)
        for fmeta in files:
            content = await database.get_bot_file(bot_id, fmeta["filename"])
            if content:
                file_path = workspace / fmeta["filename"]
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(content)

        # Create a small launcher that loads .env
        launcher = workspace / "run.py"
        launcher.write_text(
            """import os
from pathlib import Path

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

# Run the bot
import bot
""",
            encoding="utf-8",
        )

        return True, str(workspace)

    async def start_sub_bot(self, bot_id: int) -> Tuple[bool, str]:
        if self.is_running(bot_id):
            return False, "⚠️ Бот қазірдің өзінде жұмыс істеп тұр."

        ok, result = await self._prepare_workspace(bot_id)
        if not ok:
            return False, result

        workspace = Path(result)

        try:
            # Start process
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "run.py",
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,  # detach from parent signals
            )

            self.processes[bot_id] = proc
            self.restart_counts[bot_id] = self.restart_counts.get(bot_id, 0)

            await database.update_bot_status(bot_id, "running")
            await database.add_log(bot_id, "INFO", f"Bot started (PID {proc.pid})")

            # Start log reader + monitor
            self._monitor_tasks[bot_id] = asyncio.create_task(
                self._monitor_process(bot_id, proc)
            )

            return True, f"✅ Бот іске қосылды!\n🆔 PID: `{proc.pid}`"

        except Exception as e:
            logger.exception("Failed to start bot %s", bot_id)
            await database.add_log(bot_id, "ERROR", f"Start failed: {e}")
            return False, f"❌ Іске қосу қатесі:\n`{str(e)[:500]}`"

    async def _monitor_process(self, bot_id: int, proc: asyncio.subprocess.Process):
        """Read stdout and handle exit."""
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    level = "ERROR" if any(
                        x in text.lower() for x in ("error", "exception", "traceback")
                    ) else "INFO"
                    await database.add_log(bot_id, level, text)
        except Exception:
            pass

        returncode = await proc.wait()
        await database.add_log(
            bot_id, "WARNING", f"Process exited with code {returncode}"
        )

        # Cleanup
        self.processes.pop(bot_id, None)
        task = self._monitor_tasks.pop(bot_id, None)

        if returncode == 0:
            await database.update_bot_status(bot_id, "stopped")
        else:
            await database.update_bot_status(bot_id, "crashed")

            # Auto-restart logic
            if config.AUTO_RESTART:
                count = self.restart_counts.get(bot_id, 0)
                if count < config.MAX_RESTART_ATTEMPTS:
                    self.restart_counts[bot_id] = count + 1
                    await database.add_log(
                        bot_id,
                        "INFO",
                        f"Auto-restart {count + 1}/{config.MAX_RESTART_ATTEMPTS} in {config.RESTART_DELAY}s",
                    )
                    await asyncio.sleep(config.RESTART_DELAY)
                    success, msg = await self.start_sub_bot(bot_id)
                    await database.add_log(
                        bot_id, "INFO" if success else "ERROR", f"Auto-restart result: {msg}"
                    )
                else:
                    await database.add_log(
                        bot_id, "ERROR", "Max restart attempts reached. Bot stopped."
                    )
                    self.restart_counts[bot_id] = 0

    async def stop_sub_bot(self, bot_id: int) -> Tuple[bool, str]:
        proc = self.processes.get(bot_id)
        if not proc or proc.returncode is not None:
            await database.update_bot_status(bot_id, "stopped")
            return True, "ℹ️ Бот қазірдің өзінде тоқтатылған."

        try:
            # Kill process group
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=8)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()

        self.processes.pop(bot_id, None)
        task = self._monitor_tasks.pop(bot_id, None)
        if task:
            task.cancel()

        self.restart_counts[bot_id] = 0
        await database.update_bot_status(bot_id, "stopped")
        await database.add_log(bot_id, "INFO", "Bot stopped by user")

        return True, "🛑 Бот тоқтатылды."

    async def restart_sub_bot(self, bot_id: int) -> Tuple[bool, str]:
        await self.stop_sub_bot(bot_id)
        await asyncio.sleep(1)
        self.restart_counts[bot_id] = 0
        return await self.start_sub_bot(bot_id)

    async def delete_workspace(self, bot_id: int):
        if self.is_running(bot_id):
            await self.stop_sub_bot(bot_id)

        workspace = self._workspace_path(bot_id)
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

    async def stop_all(self):
        bot_ids = list(self.processes.keys())
        for bot_id in bot_ids:
            try:
                await self.stop_sub_bot(bot_id)
            except Exception as e:
                logger.error("Error stopping bot %s: %s", bot_id, e)


# Global instance
runner_manager = RunnerManager()
