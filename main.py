import os
import re
import time
import uuid
import requests
import pdfplumber

from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import ComponentType
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:27b")
PLUGIN_NAME = "paper_summarizer"


@register(PLUGIN_NAME, "Kai", "上传PDF自动中文总结Abstract", "0.0.1")
class PaperSummarizer(Star):

    def __init__(self, context: Context):
        super().__init__(context)

        self.waiting_sessions = {}

        # 兼容不同版本 get_astrbot_data_path 返回类型
        base_path = get_astrbot_data_path()
        if not isinstance(base_path, Path):
            base_path = Path(base_path)

        self.plugin_data_path = base_path / "plugin_data" / PLUGIN_NAME
        self.plugin_data_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"[INIT] 插件数据目录: {self.plugin_data_path}")

    # ==========================================================
    # /paper 指令
    # ==========================================================
    @filter.command("paper")
    async def paper_command(self, event: AstrMessageEvent):

        session_id = event.get_session_id()

        ok, msg = self.check_ollama()
        if not ok:
            yield event.plain_result(f"❌ Ollama 连接失败: {msg}")
            return

        self.waiting_sessions[session_id] = time.time() + 60

        yield event.plain_result("📄 请在 60 秒内发送 PDF 文件。")

    # ==========================================================
    # 监听所有消息
    # ==========================================================
    @filter.regex(".*")
    async def handle_all(self, event: AstrMessageEvent):

        session_id = event.get_session_id()

        if session_id not in self.waiting_sessions:
            return

        if time.time() > self.waiting_sessions[session_id]:
            del self.waiting_sessions[session_id]
            yield event.plain_result("⏳ 已超时，请重新发送 /paper")
            return

        message_chain = event.get_messages()

        for msg in message_chain:

            if msg.type != ComponentType.File:
                continue

            logger.info("[DEBUG] 收到文件组件")

            try:
                file_data = await msg.get_file()
            except Exception as e:
                logger.error(f"[ERROR] 文件下载失败: {e}")
                yield event.plain_result("❌ 文件下载失败，请重试")
                return

            # ==================================================
            # 保存文件
            # ==================================================
            file_path = self.plugin_data_path / f"{uuid.uuid4().hex}.pdf"

            try:
                if isinstance(file_data, bytes):
                    logger.info(f"[DEBUG] 文件大小: {len(file_data)} bytes")
                    with open(file_path, "wb") as f:
                        f.write(file_data)

                elif isinstance(file_data, str):
                    logger.info(f"[DEBUG] cqhttp缓存路径: {file_data}")
                    with open(file_data, "rb") as src, open(file_path, "wb") as dst:
                        dst.write(src.read())

                else:
                    logger.error("[ERROR] 未知文件格式")
                    yield event.plain_result("❌ 文件格式不支持，请重新发送")
                    return

            except Exception as e:
                logger.error(f"[ERROR] 文件保存失败: {e}")
                yield event.plain_result("❌ 文件保存失败，请重试")
                return

            logger.info(f"[DEBUG] 文件保存路径: {file_path}")

            # ==================================================
            # 校验 PDF
            # ==================================================
            if not self.is_pdf(file_path):
                logger.error("[ERROR] 非 PDF 文件")
                self.safe_delete(file_path)
                yield event.plain_result("❌ 文件不是 PDF，请重新发送")
                return

            # ==================================================
            # 提取 Abstract
            # ==================================================
            abstract = self.extract_abstract(file_path)

            if not abstract:
                logger.error("[ERROR] 未找到 Abstract")
                self.safe_delete(file_path)
                yield event.plain_result("❌ 未找到 Abstract，请确认论文包含 Abstract")
                return

            # ==================================================
            # 调用 Ollama
            # ==================================================
            summary = self.call_ollama(abstract)

            yield event.plain_result(summary)

            self.safe_delete(file_path)
            del self.waiting_sessions[session_id]
            return

    # ==========================================================
    def is_pdf(self, file_path: Path):
        try:
            with open(file_path, "rb") as f:
                return f.read(4) == b"%PDF"
        except Exception as e:
            logger.error(f"[ERROR] PDF 校验失败: {e}")
            return False

    # ==========================================================
    def extract_abstract(self, pdf_path: Path, max_pages=3):

        full_text = ""

        try:
            with pdfplumber.open(pdf_path) as pdf:
                for i in range(min(max_pages, len(pdf.pages))):
                    text = pdf.pages[i].extract_text()
                    if text:
                        full_text += "\n" + text
        except Exception as e:
            logger.error(f"[ERROR] PDF 读取失败: {e}")
            return ""

        pattern = re.compile(
            r"(Abstract|ABSTRACT)\s*(.*?)(?=\n[A-Z][a-zA-Z ]{2,}|Introduction|INTRODUCTION)",
            re.DOTALL
        )

        match = pattern.search(full_text)
        return match.group(2).strip() if match else ""

    # ==========================================================
    def check_ollama(self):
        try:
            response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
            if response.status_code != 200:
                return False, "服务未响应"

            models = response.json().get("models", [])
            model_names = [m["name"] for m in models]

            if OLLAMA_MODEL not in model_names:
                return False, f"模型 {OLLAMA_MODEL} 未找到"

            return True, "OK"

        except Exception as e:
            return False, str(e)

    # ==========================================================
    def call_ollama(self, text):

        url = f"{OLLAMA_BASE_URL}/api/generate"

        payload = {
            "model": OLLAMA_MODEL,
            "prompt": f"""
请用中文对下面论文的 Abstract 进行简洁、专业、条理清晰的总结：

{text}
""",
            "stream": False
        }

        try:
            response = requests.post(url, json=payload, timeout=300)
            if not response.ok:
                logger.error(f"[ERROR] Ollama 响应异常: {response.status_code}")
                return "调用 Ollama 失败"

            try:
                result = response.json()
            except ValueError as e:
                logger.error(f"[ERROR] Ollama 返回非 JSON: {e}")
                return "调用 Ollama 失败"

            return result.get("response", "总结失败")
        except Exception as e:
            logger.error(f"[ERROR] 调用 Ollama 失败: {e}")
            return "调用 Ollama 失败"

    # ==========================================================
    def safe_delete(self, path: Path):
        try:
            if path.exists():
                path.unlink()
                logger.info(f"[DEBUG] 已删除文件: {path}")
        except Exception as e:
            logger.error(f"[ERROR] 删除文件失败: {e}")
