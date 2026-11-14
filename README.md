# mental_descent() · Datathon FME 2026 · Reto RawTree

Recomendador en tiempo real para una tienda simulada: 10 productos ordenados por comprador en menos de 3 segundos.
Equipo: Lester, Leonardo, Alex, Artai.

- **Primera vez:** lee `docs/GUIA.md` (instalación, OpenCode/Claude Code y preguntas frecuentes).
- **Reglas para los agentes:** `AGENTS.md` (OpenCode lo lee directamente; Claude Code a través de `CLAUDE.md`).
- **Plan y roles:** `docs/ROADMAP.md` · **Qué hace cada uno:** `docs/estado/`
- **El reto:** `docs/reto.md` · **RawTree:** `docs/rawtree.md`
- `starter/` es el starter kit oficial de los organizadores (https://github.com/rawtreedb/datathon-fme-2026), sin modificar.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                # rellena TEAM_ID y RAWTREE_API_KEY
git config core.hooksPath .githooks
python -m src.validar
```
