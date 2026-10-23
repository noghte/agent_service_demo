"""Launch the upstream service with this directory's ticket-agent registry."""
import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info < (3, 12):
        raise SystemExit('Use the agent-service-tickets conda environment with Python 3.12.')
    toolkit = ROOT / 'toolkit'
    if not (toolkit / 'src/service/service.py').exists():
        raise SystemExit('Clone the toolkit into agent_service_demo/toolkit first; see README.md.')
    revision = subprocess.check_output(['git', '-C', str(toolkit), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != (ROOT / 'TOOLKIT_REVISION').read_text().strip():
        raise SystemExit('Toolkit revision differs from TOOLKIT_REVISION; follow the checkout step in README.md.')
    load_dotenv(ROOT / '.env', override=True)
    if not os.getenv('OPENAI_API_KEY') or not os.getenv('AUTH_SECRET'):
        raise SystemExit('Set OPENAI_API_KEY and AUTH_SECRET in this demo directory\'s .env.')
    output = ROOT / 'demo_output'
    output.mkdir(exist_ok=True)
    # The toolkit's built-in alias preserves our model without changing its model enum.
    os.environ.update(
        COMPATIBLE_MODEL=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
        COMPATIBLE_API_KEY=os.environ['OPENAI_API_KEY'],
        COMPATIBLE_BASE_URL='https://api.openai.com/v1',
        DEFAULT_MODEL='openai-compatible', DATABASE_TYPE='sqlite',
        SQLITE_DB_PATH=str(output / 'service-checkpoints.sqlite3'),
        LANGCHAIN_TRACING_V2='false', LANGFUSE_TRACING='false', USE_FAKE_MODEL='false')
    os.chdir(ROOT)
    # Local agents package replaces the example-agent registry; service/core/schema stay upstream.
    sys.path[:0] = [str(ROOT), str(toolkit / 'src')]
    import uvicorn
    uvicorn.run('service:app', host='127.0.0.1', port=int(os.getenv('PORT', '8081')))


if __name__ == '__main__':
    main()
