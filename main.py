"""
Gmail Newsletter Summary System - CLI Entry Point

Usage:
    python main.py auth              # Gmail OAuth2 최초 인증
    python main.py run-digest        # 전체 파이프라인 실행
    python main.py run-digest --dry-run   # 발송 없이 결과만 출력
    python main.py run-digest --lookback-hours 48
    python main.py status            # 처리 통계 확인
"""
import click
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_settings():
    """Load settings, giving a friendly error if .env is missing."""
    try:
        from config.settings import Settings
        return Settings()
    except Exception as exc:
        click.echo(f"[ERROR] 설정 로드 실패: {exc}", err=True)
        click.echo("  → .env 파일이 있는지 확인하세요: cp .env.example .env", err=True)
        sys.exit(1)


@click.group()
def cli():
    """Gmail 뉴스레터 자동 요약 시스템"""


@cli.command("auth")
def authenticate_cmd():
    """Gmail OAuth2 초기 인증 (최초 1회 실행)"""
    settings = _load_settings()

    from config.logging_config import setup_logging
    setup_logging(settings.log_level)

    click.echo("Gmail OAuth2 인증을 시작합니다...")
    click.echo(f"  credentials 경로: {settings.google_credentials_path}")
    click.echo(f"  token 저장 경로:   {settings.google_token_path}")

    credentials_path = Path(settings.google_credentials_path)
    if not credentials_path.exists():
        click.echo(
            f"\n[ERROR] credentials.json 파일을 찾을 수 없습니다: {credentials_path}\n"
            "GCP Console에서 OAuth2 자격증명을 생성하고 다운로드하세요.\n"
            "  https://console.cloud.google.com → APIs & Services → Credentials",
            err=True,
        )
        sys.exit(1)

    from gmail.auth import authenticate
    creds = authenticate(
        credentials_path=str(credentials_path),
        token_path=settings.google_token_path,
        environment=settings.environment,
    )

    click.echo(f"\n인증 성공! token 저장: {settings.google_token_path}")
    click.echo(f"계정: {getattr(creds, 'token_uri', 'N/A')}")


@cli.command("run-digest")
@click.option("--dry-run", is_flag=True, help="발송 없이 결과만 출력 (테스트용)")
@click.option("--lookback-hours", default=None, type=int,
              help="몇 시간 전 메일부터 수집할지 (기본: .env 설정)")
def run_digest(dry_run: bool, lookback_hours: int):
    """Daily Digest 파이프라인 전체 실행"""
    settings = _load_settings()

    from config.logging_config import setup_logging
    setup_logging(settings.log_level)

    logger = logging.getLogger("main")

    if dry_run:
        logger.info("=== DRY RUN 모드: 실제 발송/정리 없이 결과만 출력합니다 ===")

    from orchestrator import DigestOrchestrator
    orchestrator = DigestOrchestrator(settings)
    orchestrator.run(dry_run=dry_run, lookback_hours=lookback_hours)


@cli.command("status")
@click.option("--days", default=7, type=int, help="최근 N일 통계 (기본: 7)")
def status(days: int):
    """처리 이력 및 통계 확인"""
    settings = _load_settings()

    from config.logging_config import setup_logging
    setup_logging("INFO")

    db_path = settings.db_path
    db_file = Path(db_path)

    if not db_file.exists():
        click.echo("아직 처리 이력이 없습니다. run-digest를 먼저 실행하세요.")
        return

    from storage.database import get_db
    from storage.models import ProcessedEmail
    from datetime import date, timedelta
    from sqlalchemy import func

    cutoff = date.today() - timedelta(days=days)

    with get_db(db_path) as session:
        total = session.query(func.count(ProcessedEmail.message_id)).scalar()
        recent = (
            session.query(ProcessedEmail)
            .filter(ProcessedEmail.digest_date >= cutoff)
            .order_by(ProcessedEmail.received_at.desc())
            .all()
        )

    click.echo(f"\n📊 처리 통계 (최근 {days}일)\n{'─' * 40}")
    click.echo(f"  전체 처리 메일: {total}개")
    click.echo(f"  최근 {days}일 처리: {len(recent)}개")

    if recent:
        avg_importance = sum(e.importance_score for e in recent) / len(recent)
        click.echo(f"  평균 중요도:     {avg_importance:.1f} / 5.0")

        categories: dict = {}
        for e in recent:
            categories[e.category] = categories.get(e.category, 0) + 1
        click.echo(f"\n  카테고리별:")
        for cat, cnt in sorted(categories.items(), key=lambda x: -x[1]):
            click.echo(f"    {cat:<15} {cnt}개")

    click.echo()


@cli.command("fetch-only")
@click.option("--lookback-hours", default=24, type=int)
def fetch_only(lookback_hours: int):
    """Gmail 수집 + 필터링만 테스트 (요약/발송 없음)"""
    settings = _load_settings()

    from config.logging_config import setup_logging
    setup_logging("DEBUG")

    from gmail.auth import authenticate
    from gmail.client import GmailClient
    from gmail.message_parser import MessageParser
    from classifier.newsletter_filter import NewsletterFilter

    creds = authenticate(settings.google_credentials_path, settings.google_token_path)
    gmail = GmailClient(settings.google_credentials_path, settings.google_token_path)
    gmail.build_service()

    raw = gmail.fetch_emails(lookback_hours=lookback_hours, max_results=20)
    parser = MessageParser()
    parsed = [parser.parse(r) for r in raw]

    nf = NewsletterFilter()
    newsletters = [e for e in parsed if nf.is_newsletter(e)]

    click.echo(f"\n수집: {len(parsed)}개 → 뉴스레터: {len(newsletters)}개\n")
    for e in newsletters:
        click.echo(f"  [{nf.get_category(e):<10}] {e.subject[:60]}")
        click.echo(f"            from: {e.sender}")


@cli.command("add-sender")
@click.option("--email", "sender_email", required=True, help="허용할 발신자 이메일 주소 (예: newsletter@example.com)")
def add_sender(sender_email: str):
    """발신자 이메일을 뉴스레터 허용 목록에 추가합니다."""
    import yaml
    path = "config/allow_senders.yaml"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    emails = data.get("emails") or []
    if sender_email.lower() in [e.lower() for e in emails]:
        click.echo(f"이미 등록되어 있습니다: {sender_email}")
        return

    emails.append(sender_email.lower())
    data["emails"] = emails

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    click.echo(f"✅ 추가 완료: {sender_email}")
    click.echo(f"   다음 실행부터 이 발신자의 메일이 뉴스레터로 처리됩니다.")


@cli.command("add-domain")
@click.option("--domain", required=True, help="허용할 도메인 (예: longblack.co)")
def add_domain(domain: str):
    """도메인을 뉴스레터 허용 목록에 추가합니다."""
    import yaml
    path = "config/allow_senders.yaml"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    domains = data.get("domains") or []
    if domain.lower() in [d.lower() for d in domains]:
        click.echo(f"이미 등록되어 있습니다: {domain}")
        return

    domains.append(domain.lower())
    data["domains"] = domains

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    click.echo(f"✅ 추가 완료: {domain}")
    click.echo(f"   다음 실행부터 이 도메인의 메일이 뉴스레터로 처리됩니다.")


@cli.command("list-senders")
def list_senders():
    """현재 뉴스레터 허용 목록을 출력합니다."""
    import yaml

    click.echo("\n=== newsletter_sources.yaml (플랫폼 도메인) ===")
    with open("config/newsletter_sources.yaml", "r", encoding="utf-8") as f:
        src = yaml.safe_load(f) or {}
    for d in src.get("domains", []):
        click.echo(f"  도메인: {d}")

    click.echo("\n=== allow_senders.yaml (수동 추가) ===")
    with open("config/allow_senders.yaml", "r", encoding="utf-8") as f:
        allow = yaml.safe_load(f) or {}
    for e in (allow.get("emails") or []):
        click.echo(f"  이메일: {e}")
    for d in (allow.get("domains") or []):
        click.echo(f"  도메인: {d}")
    if not (allow.get("emails") or []) and not (allow.get("domains") or []):
        click.echo("  (수동 추가 항목 없음)")


if __name__ == "__main__":
    cli()
