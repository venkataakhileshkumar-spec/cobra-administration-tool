# COBRA Administration Tool

Simple CLI (Python 3.9+, standard library only) for tracking COBRA continuation coverage.

## Quick start
```bash
python cobra_admin.py add --name "Asha Rao" --employee "Asha Rao" --relationship employee \
    --event termination --event-date 2026-10-31 --notice-date 2026-11-10 --base-premium 650
python cobra_admin.py elect 1 --date 2026-12-01
python cobra_admin.py pay 1 --period 2026-11 --amount 663 --date 2026-12-15
python cobra_admin.py list
python cobra_admin.py status 1
python cobra_admin.py overdue
python cobra_admin.py report
```

## Rules implemented
- Max coverage: 18 months (termination, reduced hours); 36 months (divorce, legal separation, death, Medicare entitlement, dependent aging out)
- Election window: 60 days from the later of loss of coverage / notice date
- Premium: 102% of plan cost
- First premium due 45 days after election; monthly premiums have a 30-day grace period

## Not covered (ideas for extension)
Disability extension (29 months), second qualifying events, state mini-COBRA, notice letter generation.
