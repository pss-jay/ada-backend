# Ada Backend - Protocol Generation Platform

Backend server for the Altasciences Ada protocol generation platform.

## Quick Start

```bash
# 1. Copy .env.template to .env and fill in your Azure credentials
cp .env.template .env

# 2. Start the server (no external dependencies needed beyond pdfplumber + python-docx)
cd ada-backend
PYTHONPATH=$(pwd) python3 main.py

# 3. Open http://localhost:8000 in your browser
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check, shows Azure config status |
| `/api/protocols/upload` | POST | Upload SOW PDF/DOCX, auto-detects species |
| `/api/protocols/{id}/extract` | POST | Extract protocol data via Azure OpenAI |
| `/api/protocols/{id}` | GET | Review extracted JSON data |
| `/api/protocols/{id}` | PATCH | Edit extracted fields before generation |
| `/api/protocols/{id}/generate` | POST | Generate protocol DOCX from extracted data |
| `/api/protocols/{id}/download` | GET | Download the generated DOCX |

## Mock Mode

If Azure OpenAI credentials are not configured, the server runs in mock mode:
- Upload and text extraction work normally (uses pdfplumber)
- Extraction returns a schema skeleton (empty fields matching the Pydantic model structure)
- DOCX generation produces a real document using the template (with empty placeholder values)

To enable real AI extraction, add your credentials to `.env`:
```
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_KEY=your-key-here
```

## Project Structure

```
ada-backend/
├── main.py                          # HTTP server (stdlib, no FastAPI needed)
├── pydantic/                        # Lightweight pydantic shim
├── app/
│   ├── integrations/
│   │   ├── azure_openai/            # AI extraction pipeline
│   │   │   ├── schema_models.py     # Pydantic models (200+ fields)
│   │   │   ├── primary_prompt.py    # Prompt generator
│   │   │   └── prompt_helper.py     # Azure OpenAI client
│   │   └── docx_gen/               # Document generation
│   │       ├── docx_generator.py    # Template population
│   │       ├── flatten.py           # JSON flattener
│   │       └── animal_detector.py   # Species detection
│   ├── services/
│   │   └── pdf_parser.py           # PDF text extraction
│   └── data/                       # Protocol JSON storage (Phase 1)
├── templates/                      # 3 DOCX templates (rodent, dog, swine)
└── output/                         # Generated protocol files
```

## Supported Species

- **Rat/Rodent** - 1255+ protocol fields
- **Dog/Canine** - 1103+ protocol fields
- **Swine/Minipig** - 1190+ protocol fields

## Dependencies

Pre-installed (standard library + common packages):
- `pdfplumber` - PDF text extraction
- `python-docx` - DOCX template reading/writing
- `requests` - Azure OpenAI API calls
- `python-dotenv` - Environment variable loading
