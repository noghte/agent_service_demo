# Governed ticket agent with Agent Service Toolkit

Open [ticket_agent_demo.ipynb](ticket_agent_demo.ipynb). It repeats the Langflow demo using [JoshuaC215/agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit): the first 15 CSV tickets, four policies, a real OpenAI model, SQLite state, category memory, and Microsoft Agent Governance Toolkit auditing.

Expected result: **14 response drafts and one human-review item**. TKT-100000 has a synthetic seven-day inactivity scenario. POL-003 permits closure after five days; POL-004 forbids General Inquiry closure before ten. Governance blocks closure and the model writes an internal escalation note. All actions are local simulations.

## Setup

Run inside this directory. Use Python 3.12 in a separate conda environment; the upstream toolkit requires Python 3.12 or newer.

```bash
conda create -n agent-service-tickets python=3.12 pip -y
conda activate agent-service-tickets
git clone https://github.com/JoshuaC215/agent-service-toolkit.git toolkit
git -C toolkit checkout fe3b2dcc84d41e6f7f345fd5830f2e12a978c10a
python -m pip install ./toolkit -r requirements-demo.txt
python -m ipykernel install --user --name agent-service-tickets --display-name "Python (agent-service-tickets)"
```

For an existing clone, skip cloning and run checkout/install. The selected upstream revision is recorded in [TOOLKIT_REVISION](TOOLKIT_REVISION); the launcher checks it. The full toolkit dependency install can take several minutes. No Docker or uv is required.

The local `.env` contains the copied OpenAI key/model and a new service authentication token. It has owner-only permissions and is ignored by git. Langflow credentials and Docker passwords were not copied. If `.env` is absent in a distributed copy, copy `.env.example`, supply your OpenAI key, and replace `AUTH_SECRET` with a long random token:

```dotenv
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-4.1-mini
AUTH_SECRET=your-long-random-service-token
PORT=8081
```

Start the service in the first terminal:

```bash
conda activate agent-service-tickets
python run_service.py
```

`Application startup complete` means the service has started. With the default port, open http://127.0.0.1:8081/health to check for `{"status":"ok"}`. This service has no homepage: opening http://127.0.0.1:8081/ returns `404 Not Found`, as do automatic browser requests for `/favicon.ico` and Apple touch icons. These responses do not indicate a startup failure. The notebook is the demo interface.

In a second terminal, also in this directory:

```bash
conda activate agent-service-tickets
python -m jupyter lab ticket_agent_demo.ipynb
```

Select **Python (agent-service-tickets)** and run cells in order. The notebook displays `/info`, then calls `/ticket-agent/invoke`. API documentation is at http://127.0.0.1:8081/docs. Keep the service terminal open for diagnostics. Restart it after changing `.env`, agent code, or instructions. Restart the notebook kernel after updating helper files. Ctrl+C stops the service; saved files remain.

The launcher maps `OPENAI_MODEL` onto the toolkit's built-in `openai-compatible` model alias at `https://api.openai.com/v1`. This keeps the original model without modifying the toolkit's model enum. The request uses the alias; prompts, fingerprints, and audit records identify the actual OpenAI model. The OpenAI key stays in the service process, while notebook requests use `AUTH_SECRET`. Optional LangSmith/Langfuse tracing is disabled.

## Walkthrough

Read the four policy texts, inspect the first ticket's context and conflict, and run the batch. Review its single escalation note. Rerun the batch cell to show cached decisions without more model requests. Inspect category memory, then verify the signed audit and reject a tampered copy. The notebook has eight code cells; reusable plumbing is in Python modules.

Expected outputs appear as comments instead of assertions. The cells display actual status counts and audit validity. Policy enforcement, structured-response validation, and redacted diagnostics stay in the helpers. Audit checkpoints are saved only when the log verifies.

This directory contains independent CSV copies, credentials, and a separate `demo_output/`. Its files and outputs are independent of the Langflow demo.

| File | Purpose |
| --- | --- |
| [run_service.py](run_service.py) | Loads this `.env` and starts the upstream FastAPI application on localhost:8081. |
| [agents/__init__.py](agents/__init__.py) | Registers only the ticket agent through the interface consumed by the toolkit service. |
| [agents/ticket_agent.py](agents/ticket_agent.py) | LangGraph agent using the toolkit model factory and OpenAI structured output. |
| [ticket_contract.py](ticket_contract.py) | Shared instructions, three-field plan schema, and error redaction. |
| [demo_support.py](demo_support.py) | Host policy checks, redaction, HTTP requests, saved decisions, and signed audit. |

The launcher places the local `agents` package before `toolkit/src` on the import path. The service, schemas, model factory, and checkpoint initialization remain upstream. This isolates our registry from unrelated example agents without editing the cloned toolkit.

State is in `demo_output/state.sqlite3`. Service thread checkpoints are in `demo_output/service-checkpoints.sqlite3`. Category memory comes from saved business decisions; the toolkit's default SQLite configuration uses a volatile cross-thread Store, which this demo does not use for durable memory. Results are exported to `processed_tickets.json` and `human_review.json`. Audit JSONL, owner-readable `.audit-key`, and checkpoint files live in the same output directory. Service run/thread IDs correlate model proposals with governance records.

## Checks and limits

In the configured environment:

```bash
python -m unittest -v test_demo test_agent
```

Thirteen checks cover notebook syntax/format, first-15 selection, policy boundaries, one conflict, final-action validation, malformed responses, provider/HTTP failures, secret redaction, cached state, memory, exports, audit tampering/truncation, system/user message separation, and real LangGraph checkpoints. They also check that invalid audits do not create checkpoints and that notebook cells contain no assertions. Test HTTP/model responses are substituted; there is no production fake-planner mode.

Locally, the eleven governance/notebook checks passed with the installed governance toolkit 4.1.0, and the two agent checks passed with LangGraph 1.0.2. The complete upstream dependency installation and a live OpenAI/service run were not executed: shell network access is restricted. The tests do not establish compatibility of every dependency in the pinned toolkit installation.

Lifecycle facts are synthetic, redaction covers names/emails, and escalation is a local review artifact. State and audit writes are separate transactions, so a crash can require reconciliation. Protect the audit key and retain checkpoints separately for stronger tamper evidence. Memory counts drafts/reviews rather than proven resolutions. The model consumes the latest bounded context while the service retains thread checkpoints.

For a fresh presentation, stop the service, rename this directory's `demo_output`, and restart the service and notebook. Retain old audit files, keys, and checkpoints together when keeping previous evidence.

The notebook includes an appendix on [LangChain's context-engineering article](https://www.langchain.com/blog/context-engineering-deep-agents-remember). OpenAI behavior follows [structured output documentation](https://developers.openai.com/api/docs/guides/structured-outputs) and the [GPT-4.1 mini model documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
