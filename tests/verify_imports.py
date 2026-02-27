"""팀 구현 완료 검증 스크립트"""
import sys
sys.path.insert(0, '.')

checks = [
    ("config.settings",            "from config.settings import Settings"),
    ("config.logging_config",      "from config.logging_config import setup_logging"),
    ("storage.models",             "from storage.models import ProcessedEmail"),
    ("storage.database",           "from storage.database import init_db, save_processed_emails"),
    ("gmail.auth",                 "from gmail.auth import authenticate"),
    ("gmail.client",               "from gmail.client import GmailClient"),
    ("gmail.message_parser",       "from gmail.message_parser import MessageParser, ParsedEmail"),
    ("classifier.newsletter_filter","from classifier.newsletter_filter import NewsletterFilter"),
    ("classifier.deduplicator",    "from classifier.deduplicator import Deduplicator"),
    ("summarizer.prompt_builder",  "from summarizer.prompt_builder import build_analysis_prompt"),
    ("summarizer.claude_client",   "from summarizer.claude_client import ClaudeClient, SummaryResult"),
    ("organizer.gmail_organizer",  "from organizer.gmail_organizer import GmailOrganizer, OrganizeStats"),
    ("digest.digest_builder",      "from digest.digest_builder import DigestBuilder, DigestEmail"),
    ("digest.sender",              "from digest.sender import DigestSender"),
    ("orchestrator",               "from orchestrator import DigestOrchestrator"),
    ("main (CLI)",                 "import main"),
]

print("╔════════════════════════════════════════════════════╗")
print("║   Gmail 뉴스레터 시스템 - Import 검증 결과         ║")
print("╠════════════════════════════════════════════════════╣")

ok = 0
for name, stmt in checks:
    try:
        exec(stmt)
        print(f"║  ✅ {name:<35}║")
        ok += 1
    except Exception as e:
        msg = str(e)[:30]
        print(f"║  ❌ {name:<20} {msg:<15}║")

print("╠════════════════════════════════════════════════════╣")
status = "✅ 전체 통과!" if ok == len(checks) else f"⚠️  {len(checks)-ok}개 실패"
print(f"║  결과: {ok}/{len(checks)} 통과  {status:<25}║")
print("╚════════════════════════════════════════════════════╝")

print("\n[다음 실행 순서]")
print("  1. cp .env.example .env  → API 키 입력")
print("  2. data/ 폴더에 credentials.json 복사")
print("  3. .venv/bin/python main.py auth")
print("  4. .venv/bin/python main.py run-digest --dry-run")
