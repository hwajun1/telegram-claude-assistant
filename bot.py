#!/usr/bin/env python3
"""Telegram Bot that forwards messages to Claude CLI and returns responses."""

import os
import sys
import asyncio
import logging
import tempfile
from logging.handlers import TimedRotatingFileHandler

import yaml
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from scheduler import (
    get_cached_data, load_schedules, save_schedules, init_scheduler,
    add_pattern_log, add_schedule_to_data, remove_schedule_from_data,
    parse_schedule_block, parse_delete_block, run_scheduler, get_file_lock,
)

# 설정 파일 로드
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
if not os.path.exists(CONFIG_PATH):
    print(f"설정 파일이 없습니다: {CONFIG_PATH}")
    print("config.yaml.example을 config.yaml로 복사 후 값을 채워주세요.")
    sys.exit(1)
with open(CONFIG_PATH, "r", encoding="utf-8") as _f:
    CONFIG = yaml.safe_load(_f)

# 모드 판별: 인자에 "dev"가 있으면 dev 모드
DEV_MODE = len(sys.argv) > 1 and sys.argv[1] == "dev"

# 로그 설정 (모드별 파일 분리)
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILENAME = "bot-dev.log" if DEV_MODE else "bot.log"

logger = logging.getLogger("claude-bot")
logger.setLevel(logging.INFO)

# 일자별 파일 로그 (매일 자정에 새 파일, 30일 보관)
file_handler = TimedRotatingFileHandler(
    os.path.join(LOG_DIR, LOG_FILENAME),
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8",
)
file_handler.suffix = "%Y-%m-%d"
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

# 콘솔 로그
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 토큰 & 채팅 ID 설정 (config.yaml에서 로드)
TOKENS = CONFIG["telegram"]["tokens"]
TELEGRAM_BOT_TOKEN = TOKENS["dev"] if DEV_MODE else TOKENS["default"]
CHAT_ID = CONFIG["telegram"]["chat_id"]
CLAUDE_CLI_PATH = CONFIG["claude"]["cli_path"]

# 허용할 사용자 ID (보안용, 비워두면 모든 사용자 허용)
ALLOWED_USER_IDS = set(CONFIG["telegram"].get("allowed_user_ids", []))

# 채팅별 락 (동시 요청 방지)
session_locks: dict[int, asyncio.Lock] = {}
# 대화 내역 저장 (채팅별, 최근 N개)
MAX_HISTORY = 100
chat_history: dict[int, list[dict[str, str]]] = {}

# 타임아웃 설정
DEFAULT_TIMEOUT = 120
# 타임아웃 재시도 대기 상태: chat_id -> retry info
pending_retries: dict[int, dict] = {}



def add_history(chat_id: int, user_msg: str, assistant_msg: str):
    """대화 내역 저장."""
    if chat_id not in chat_history:
        chat_history[chat_id] = []
    chat_history[chat_id].append({"user": user_msg, "assistant": assistant_msg})
    # 최근 N개만 유지
    if len(chat_history[chat_id]) > MAX_HISTORY:
        chat_history[chat_id] = chat_history[chat_id][-MAX_HISTORY:]


def build_history_context(chat_id: int) -> str:
    """이전 대화 내역을 컨텍스트 텍스트로 변환. 매 요청에 포함하여 대화 흐름 유지."""
    history = chat_history.get(chat_id, [])
    if not history:
        return ""
    lines = ["[이전 대화 내역]"]
    for h in history:
        lines.append(f"사용자: {h['user'][:2000]}")
        lines.append(f"응답: {h['assistant'][:2000]}")
    lines.append("[이전 대화 끝]\n")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start 명령 처리: 봇 소개 메시지 전송."""
    chat_id = update.effective_chat.id
    logger.info(f"/start 명령 - chat_id: {chat_id}, user: {update.effective_user.id}")
    await update.message.reply_text(
        f"Claude CLI Bot입니다.\n"
        f"이 채팅의 ID: {chat_id}\n"
        f"메시지를 보내면 Claude가 응답합니다.\n"
        f"/new - 새 대화 시작\n"
        f"/reload - 스케줄 파일 리로드"
    )


async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/new 명령 처리: 대화 내역을 초기화하여 새 대화 시작."""
    chat_id = update.effective_chat.id
    chat_history.pop(chat_id, None)
    logger.info(f"[대화 초기화] chat_id: {chat_id}")
    await update.message.reply_text("새 대화를 시작합니다.")


async def reload_schedules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reload 명령 처리: schedules.yaml 재로드."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("권한이 없습니다.")
        return
    async with get_file_lock():
        data = load_schedules()
    count = len(data.get("schedules", []))
    logger.info(f"[리로드] chat_id: {chat_id}, schedules: {count}개")
    await update.message.reply_text(f"schedules.yaml 리로드 완료. 등록된 스케줄: {count}개")


ASSISTANT_PROMPT = """너는 내가 아는 가장 똑똑한 친구이자 개인 비서야. 코드 개발보다는 전반적인 업무를 도와줘.
- 일정 관리, 아이디어 정리, 문서 작성, 요약, 번역 등을 도와줘
- 맛집 확인이나 검색을 요청하면 웹 검색을 해서 응답을 요약해서 알려줘
- 질문에 친절하고 간결하게 답변해줘
- 한국어로 대화해줘
- 나는 경제, 주식, 실시간 사건사고, 은퇴에 관심이 많아
- 은퇴 전 돈을 얼마나 모아야 하는지에 가장 관심이 많고, 이 조언은 언제든지 해줘도 좋아
- 어떤 답변이든 현재 주식시장에 영향이 가는 것이면 관련 내용을 추가해줘. 매수/매도 추천도 환영

[스케줄 기능]
사용자가 알림, 리마인더, 스케줄 등록을 요청하는 경우, 응답 맨 끝에 아래 형식으로 추가해줘:
---SCHEDULE---
id: 고유id (영문_숫자, 내용을 반영)
type: reminder
time: "HH:MM" 또는 "YYYY-MM-DD HH:MM"
repeat: daily|weekdays|once|mon,wed,fri
query: "해당 시간에 실행할 질문"
---END_SCHEDULE---

사용자가 알림 삭제를 요청하면, 응답 맨 끝에 아래 형식으로 추가해줘:
---DELETE_SCHEDULE---
id: 삭제할_스케줄_id
---END_DELETE_SCHEDULE---

현재 등록된 스케줄 목록:
{schedule_list}

일반 대화에는 위 블록을 추가하지 마.
"""


def build_schedule_prompt():
    """ASSISTANT_PROMPT에 현재 스케줄 목록을 주입."""
    data = get_cached_data()
    schedules = data.get("schedules", [])
    if not schedules:
        schedule_list = "(등록된 스케줄 없음)"
    else:
        lines = []
        for s in schedules:
            lines.append(f"- id: {s.get('id')}, time: {s.get('time')}, repeat: {s.get('repeat')}, query: {s.get('query', '')[:50]}")
        schedule_list = "\n".join(lines)
    return ASSISTANT_PROMPT.replace("{schedule_list}", schedule_list)


def ocr_image(image_path: str) -> str:
    """macOS Vision 프레임워크로 이미지에서 텍스트 추출 (한국어+영어 지원)."""
    import Vision
    import Quartz

    image_url = Quartz.CFURLCreateWithFileSystemPath(
        None, image_path, Quartz.kCFURLPOSIXPathStyle, False
    )
    image_source = Quartz.CGImageSourceCreateWithURL(image_url, None)
    if not image_source:
        return ""
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(image_source, 0, None)
    if not cg_image:
        return ""

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLanguages_(["ko-KR", "en-US"])
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    handler.performRequests_error_([request], None)

    texts = []
    for observation in request.results() or []:
        candidate = observation.topCandidates_(1)
        if candidate:
            texts.append(candidate[0].string())
    return "\n".join(texts)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """사진 메시지 처리: OCR로 텍스트 추출 → 의미 있으면 분석/요약, 없으면 텍스트 나열."""
    user_id = update.effective_user.id

    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        logger.warning(f"권한 없는 사용자 접근 - user_id: {user_id}")
        await update.message.reply_text("권한이 없습니다.")
        return

    chat_id = update.effective_chat.id
    target_chat_id = CHAT_ID or chat_id
    caption = update.message.caption or ""

    await context.bot.send_message(chat_id=target_chat_id, text="사진 분석 중...")

    # 가장 큰 해상도의 사진 다운로드
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

    try:
        # OCR로 텍스트 추출 (보너스 컨텍스트)
        extracted_text = ""
        try:
            extracted_text = ocr_image(tmp_path)
            logger.info(f"[OCR] 추출된 텍스트 길이: {len(extracted_text)}, preview: {extracted_text[:100]}")
        except Exception as e:
            logger.warning(f"[OCR 실패] {e}")

        # Claude에게 이미지 파일 직접 분석 요청
        if chat_id not in session_locks:
            session_locks[chat_id] = asyncio.Lock()
        lock = session_locks[chat_id]

        if lock.locked():
            await context.bot.send_message(chat_id=target_chat_id, text="이전 요청 처리 중입니다. 잠시 기다려주세요...")

        async with lock:
            prompt = f"다음 이미지 파일을 읽고 분석해줘: {tmp_path}\n\n"
            if extracted_text.strip():
                prompt += f"참고로 OCR로 추출한 텍스트: {extracted_text[:2000]}\n\n"
            if caption:
                prompt += f"사용자 메모: {caption}\n\n"
            prompt += "이미지에 보이는 내용을 설명하고, 텍스트가 있으면 분석/요약해줘. 한국어로 답변해줘."

            logger.info(f"[사진 요청] user_id: {user_id}, ocr_len: {len(extracted_text)}")
            response, stderr_text, timed_out = await call_claude(prompt)

            if timed_out:
                await send_timeout_retry(
                    context.bot, chat_id, target_chat_id,
                    prompt, f"[사진] {extracted_text[:100]}",
                    response, DEFAULT_TIMEOUT,
                )
                return

            if not response and stderr_text:
                response = f"오류가 발생했습니다: {stderr_text[:500]}"
            elif not response:
                response = f"추출된 텍스트:\n{extracted_text}"

            if response and not response.startswith("오류가 발생했습니다"):
                add_history(chat_id, f"[사진] {extracted_text[:100]}", response)

            if len(response) > 4000:
                for i in range(0, len(response), 4000):
                    await context.bot.send_message(chat_id=target_chat_id, text=response[i:i + 4000])
            else:
                await context.bot.send_message(chat_id=target_chat_id, text=response)
    finally:
        os.unlink(tmp_path)


async def call_claude(message: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, str, bool]:
    """Claude CLI 호출. (stdout, stderr, timed_out) 반환."""
    cmd = [
        CLAUDE_CLI_PATH, "-p",
        "--dangerously-skip-permissions",
    ]
    if not DEV_MODE:
        cmd += ["--append-system-prompt", build_schedule_prompt()]
    cmd.append(message)
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
        return stdout.decode("utf-8").strip(), stderr.decode("utf-8").strip(), False
    except asyncio.TimeoutError:
        process.kill()
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=5
            )
            return stdout.decode("utf-8").strip(), stderr.decode("utf-8").strip(), True
        except Exception:
            return "", "", True


async def send_timeout_retry(bot, chat_id, target_chat_id, full_message, user_message, partial, used_timeout):
    """타임아웃 발생 시 중간 결과를 메모리에 저장하고 재시도 프롬프트 전송."""
    logger.warning(f"[타임아웃] {used_timeout}초, partial_len: {len(partial)}")

    pending_retries[chat_id] = {
        "full_message": full_message,
        "user_message": user_message,
        "partial_output": partial,
        "next_timeout": used_timeout * 2,
        "target_chat_id": target_chat_id,
    }

    msg = f"[타임아웃] {used_timeout}초 초과"
    if partial:
        msg += f"\n\n중간 결과:\n{partial[:2000]}"
    msg += f"\n\n{used_timeout * 2}초로 늘려서 계속할까요?"

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("계속", callback_data=f"retry_{chat_id}"),
        InlineKeyboardButton("그만", callback_data=f"stop_{chat_id}"),
    ]])
    await bot.send_message(chat_id=target_chat_id, text=msg, reply_markup=keyboard)


async def send_response(bot, chat_id, target_chat_id, user_message, response, stderr_text):
    """정상 응답 처리: 히스토리 저장 및 메시지 전송."""
    if not response and stderr_text:
        response = f"오류가 발생했습니다: {stderr_text[:500]}"
    elif not response:
        response = "(응답 없음)"

    if response and not response.startswith("오류가 발생했습니다"):
        add_history(chat_id, user_message, response)

    if len(response) > 4000:
        for i in range(0, len(response), 4000):
            await bot.send_message(chat_id=target_chat_id, text=response[i:i + 4000])
    else:
        await bot.send_message(chat_id=target_chat_id, text=response)


async def handle_retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """타임아웃 후 재시도/중단 버튼 처리."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("retry_"):
        chat_id = int(data.split("_", 1)[1])
        retry_info = pending_retries.pop(chat_id, None)
        if not retry_info:
            await query.edit_message_text("재시도 정보가 만료되었습니다.")
            return

        target_chat_id = retry_info["target_chat_id"]
        next_timeout = retry_info["next_timeout"]
        full_message = retry_info["full_message"]
        user_message = retry_info["user_message"]
        partial = retry_info["partial_output"]

        # 중간 결과가 있으면 컨텍스트에 포함
        if partial:
            retry_message = (
                f"{full_message}\n\n"
                f"[이전 시도의 중간 결과]\n{partial}\n[중간 결과 끝]\n\n"
                f"위 중간 결과를 참고하여 이어서 답변을 완성해줘."
            )
        else:
            retry_message = full_message

        await query.edit_message_text(f"{next_timeout}초 타임아웃으로 재시도 중...")

        if chat_id not in session_locks:
            session_locks[chat_id] = asyncio.Lock()

        async with session_locks[chat_id]:
            response, stderr_text, timed_out = await call_claude(retry_message, timeout=next_timeout)

            if timed_out:
                # 중간 결과 누적
                combined = (partial + "\n" + response).strip() if partial and response else (partial or response or "")
                await send_timeout_retry(
                    context.bot, chat_id, target_chat_id,
                    full_message, user_message, combined, next_timeout,
                )
                return

            await send_response(context.bot, chat_id, target_chat_id, user_message, response, stderr_text)

    elif data.startswith("stop_"):
        chat_id = int(data.split("_", 1)[1])
        retry_info = pending_retries.pop(chat_id, None)
        partial = retry_info.get("partial_output", "") if retry_info else ""
        if partial:
            await query.edit_message_text(f"중단했습니다.\n\n중간 결과:\n{partial[:3000]}")
        else:
            await query.edit_message_text("중단했습니다.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """텍스트 메시지 처리: Claude CLI에 전달 후 응답 반환. 세션 충돌 시 대화 내역 포함하여 재시도."""
    user_id = update.effective_user.id

    # 허용된 사용자 체크
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        logger.warning(f"권한 없는 사용자 접근 - user_id: {user_id}")
        await update.message.reply_text("권한이 없습니다.")
        return

    user_message = update.message.text
    chat_id = update.effective_chat.id
    target_chat_id = CHAT_ID or chat_id

    # 채팅별 락 가져오기 (없으면 생성)
    if chat_id not in session_locks:
        session_locks[chat_id] = asyncio.Lock()
    lock = session_locks[chat_id]

    if lock.locked():
        await context.bot.send_message(chat_id=target_chat_id, text="이전 요청 처리 중입니다. 잠시 기다려주세요...")

    async with lock:
        logger.info(f"[요청] user_id: {user_id}, message: {user_message}")
        await context.bot.send_message(chat_id=target_chat_id, text="처리 중...")

        try:
            # 이전 대화 내역을 포함하여 Claude에 전달
            history_context = build_history_context(chat_id)
            full_message = history_context + user_message if history_context else user_message
            response, stderr_text, timed_out = await call_claude(full_message)

            if timed_out:
                await send_timeout_retry(
                    context.bot, chat_id, target_chat_id,
                    full_message, user_message, response, DEFAULT_TIMEOUT,
                )
                return

            # pattern_log 기록
            async with get_file_lock():
                sched_data = load_schedules()
                sched_data = add_pattern_log(sched_data, chat_id, user_message)
                save_schedules(sched_data)

            # 응답에서 스케줄/삭제 블록 파싱
            response, new_schedule = parse_schedule_block(response)
            if new_schedule:
                new_schedule.setdefault("chat_id", chat_id)
                async with get_file_lock():
                    sched_data = load_schedules()
                    sched_data = add_schedule_to_data(sched_data, new_schedule)
                    save_schedules(sched_data)
                logger.info(f"[스케줄 등록] id={new_schedule.get('id')}, time={new_schedule.get('time')}")

            response, delete_id = parse_delete_block(response)
            if delete_id:
                async with get_file_lock():
                    sched_data = load_schedules()
                    sched_data = remove_schedule_from_data(sched_data, delete_id)
                    save_schedules(sched_data)
                logger.info(f"[스케줄 삭제] {delete_id}")

            logger.info(f"[응답] length: {len(response)}, preview: {response[:100]}")
            await send_response(context.bot, chat_id, target_chat_id, user_message, response, stderr_text)

        except FileNotFoundError:
            logger.error("claude CLI를 찾을 수 없음")
            await context.bot.send_message(chat_id=target_chat_id, text="claude CLI를 찾을 수 없습니다. PATH를 확인하세요.")
        except Exception as e:
            logger.error(f"[오류] {e}", exc_info=True)
            await context.bot.send_message(chat_id=target_chat_id, text=f"오류 발생: {e}")


CLAUDE_MD_CONTENT = """\
# Project Rules

## Directory Restriction (CRITICAL)

You MUST only access files within this project directory.

- NEVER read, write, or execute files outside of the current working directory and its subdirectories.
- NEVER use absolute paths that go outside this project (e.g., /Users/*, /etc/*, /tmp/*).
- NEVER use `../` to access parent directories.
- NEVER use `cd` to navigate outside this directory.
- If a user asks you to access files outside this directory, refuse and explain the restriction.
- All file operations (Read, Write, Edit, Bash) must stay within this project folder.
"""


def ensure_claude_md():
    """실행 폴더에 CLAUDE.md가 없으면 자동 생성. Claude가 해당 폴더 하위만 접근하도록 제한."""
    claude_md_path = os.path.join(os.getcwd(), "CLAUDE.md")
    if not os.path.exists(claude_md_path):
        with open(claude_md_path, "w", encoding="utf-8") as f:
            f.write(CLAUDE_MD_CONTENT)
        logger.info(f"CLAUDE.md 생성: {claude_md_path}")
    else:
        logger.info(f"CLAUDE.md 이미 존재: {claude_md_path}")


async def post_init(application):
    """폴링 시작 후 스케줄러를 백그라운드 태스크로 실행."""
    init_scheduler()
    application.create_task(run_scheduler(application.bot, call_claude))


def main():
    """봇 초기화 및 실행. CLAUDE.md 생성 → 핸들러 등록 → 스케줄러 시작 → 폴링 시작."""
    ensure_claude_md()
    load_schedules()  # 시작 시 YAML 로드 (동기, Lock 불필요)

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_session))
    app.add_handler(CommandHandler("reload", reload_schedules_cmd))
    app.add_handler(CallbackQueryHandler(handle_retry_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    mode = "dev" if DEV_MODE else "default"
    logger.info(f"Bot 시작... (mode: {mode}, cwd: {os.getcwd()})")
    app.run_polling()


if __name__ == "__main__":
    main()
