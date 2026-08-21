# Long Weekend Planner

Find the long weekends hiding in your calendar, and plan a year (or two) of PTO
around them.

Given the holidays your employer actually observes, it works out which ones
already give you a long weekend, which ones are one day of PTO away from a
four-day break, and — if you tell it how many PTO days you get — the best way
to spend the whole year's allowance.

```
$ long-weekend --preset corporate --pto 15

corporate - 12 holidays, Mon-Fri workweek, nearest working day observance

2026   15 PTO day(s) available
------------------------------------------------------------------------
  Holidays
    Thu Jan 01  New Year's Day
    Mon Jan 19  Martin Luther King Jr. Day
    ...
    Fri Jul 03  Independence Day   (falls on Sat Jul 04, observed here)
    ...

  Planned breaks (4+ consecutive days off)
     4 days  Thu Jan 01 - Sun Jan 04, 2026        1 PTO  take Jan 02
              covers New Year's Day
     9 days  Sat Nov 21 - Sun Nov 29, 2026        3 PTO  take Nov 23, Nov 24, Nov 25
              covers Thanksgiving; Day After Thanksgiving
    ...

  15 of 15 PTO day(s) used in 2026
```

## Install

No runtime dependencies — the standard library is enough.

```
pip install -e .
```

That gives you the `long-weekend` command. You can also run it without
installing, either as `python3 -m longweekend.cli` or through the original
`python3 holiday.py` entry point, which still accepts its old arguments.

PyYAML is optional, and only needed for `.yaml` company configs:
`pip install -e ".[yaml]"`.

## Usage

```
long-weekend [options]
```

With no arguments it reports the US federal holidays for **the current year and
the next one**, and what each is worth. The window follows the clock; nothing is
hard-coded to a particular year.

```
long-weekend                          # this year and next
long-weekend --years 3                # this year plus the next two
long-weekend --start-year 2030 --end-year 2032
long-weekend --from-today             # skip days that have already passed
```

## Which holidays your employer observes

No two employers close for the same days, so nothing is baked in. Start from a
preset and adjust it, or describe your company in a config file.

```
long-weekend --list-presets
```

| Preset | What it is |
| --- | --- |
| `federal` (default) | All 11 US federal holidays, Juneteenth included |
| `corporate` | What a generous private employer typically closes for |
| `minimal` | The six almost every US employer observes |
| `shutdown` | Closed from Christmas Eve through New Year's Day |
| `everything` | Every holiday in the catalog, to `--drop` your way down from |
| `standard`, `nonstandard` | The two lists the original script hard-coded |

Presets are a starting point, not a claim about your employer. The federal list
is the one the government and the banks keep; private employers diverge from it
in both directions, and the ones they most often skip are Columbus Day and
Veterans Day, while the ones they most often add are the day after Thanksgiving
and Christmas Eve. (The [BLS National Compensation Survey holiday
profiles](https://www.bls.gov/ebs/factsheets/holiday-profiles.htm) publish who
actually gets which day.) That is what `--add` and `--drop` are for.

### Adding a holiday by name

Holidays the tool already knows can be added by name alone — no date needed:

```
long-weekend --list-known                        # the whole catalog
long-weekend --add "Good Friday" --add "Election Day"
long-weekend --preset minimal --add "Black Friday" --add "Christmas Eve"
```

The catalog covers the eleven federal holidays plus the days employers commonly
bolt on: Good Friday, Easter Monday, Christmas Eve, New Year's Eve, the day
after Thanksgiving, the day after Christmas, Election Day, and Indigenous
Peoples' Day. Common aliases work too, so `--add "Black Friday"`, `--add "MLK
Day"` and `--add "Washington's Birthday"` all land on the right rule.

Two names for the same day are rejected rather than silently observed twice, so
if you prefer Indigenous Peoples' Day, swap it in:

```
long-weekend --drop "Columbus Day" --add "Indigenous Peoples' Day"
```

Adjust a preset with `--add` and `--drop`:

```
long-weekend --preset federal \
  --drop "Columbus Day,Veterans Day" \
  --add "Day After Thanksgiving=Thanksgiving+1" \
  --add "Christmas Eve=12-24" \
  --add "Founders Day=2nd FRI in MAY"
```

### Holiday date forms

`--add` and config files accept a bare name from the catalog, or any of these
forms:

| Form | Meaning |
| --- | --- |
| `Good Friday` | A holiday the tool already knows (`--list-known`) |
| `Name=07-04` | The same month and day every year |
| `Name=2026-12-28` | A one-off date, e.g. an announced shutdown day |
| `Name=3rd MON in JAN` | The nth weekday of a month (`last` also works) |
| `Name=Thanksgiving+1` | An offset from another holiday in the same set |
| `Name=Easter-2` | An offset from Easter Sunday |
| `Name=TUE on or after 11-02` | The first given weekday on or after a date |

The offset form is anchored to the other holiday's *actual* date, so
"Day After Thanksgiving" is right in every year — including the years where the
fourth Friday of November falls a week *before* the fourth Thursday.

The Easter form covers the moving Christian holidays, computed with the
Gregorian computus (Good Friday is `Easter-2`, Easter Monday `Easter+1`). The
"on or after" form covers the holidays the nth-weekday rule cannot express —
US Election Day is the Tuesday after the first Monday in November, which is
exactly the first Tuesday on or after November 2.

### Weekend holidays

When a holiday lands on a day you would not have worked, `--observance`
decides what happens:

| Mode | Behaviour |
| --- | --- |
| `nearest` (default) | Nearest working day, ties going earlier — the US federal rule: Saturday to Friday, Sunday to Monday |
| `next` | The next working day |
| `previous` | The previous working day |
| `none` | Observed on the day itself; you simply lose it |

If two holidays would shift onto the same day, the second one moves further out
rather than disappearing.

### Non-standard workweeks

`--workweek` takes a range or an explicit list, and everything else follows from
it — including which days count as a "weekend" for observance purposes.

```
long-weekend --workweek SUN-THU        # Sunday to Thursday
long-weekend --workweek MON-THU        # four-day week
long-weekend --workweek MON,WED,FRI    # explicit days
```

### Config files

For anything you would otherwise retype, use a JSON or YAML config. See
[`examples/acme.json`](examples/acme.json),
[`examples/floating.json`](examples/floating.json) and
[`examples/four-day-week.yaml`](examples/four-day-week.yaml).

```jsonc
{
  "name": "Acme Corp",
  "preset": "federal",
  "drop": ["Columbus Day", "Veterans Day"],
  "add": ["Day After Thanksgiving=Thanksgiving+1", "Christmas Eve=12-24"],
  "workweek": "MON-FRI",
  "observance": "nearest",
  "pto_days": 18,
  "pto_by_year": { "2027": 23 },
  "floating_days": 2,
  "floating_by_year": { "2027": 3 },
  "carryover": 5,
  "blackout": ["2026-09-28:2026-10-09"],
  "committed": ["2026-07-20:2026-07-24"]
}
```

Use `holidays` instead of `preset` to define the whole set from scratch.
Command-line flags override whatever the file says.

```
long-weekend --config examples/acme.json --pto 20
```

## Planning PTO

Pass `--pto` and the planner works out which working days to take.

```
long-weekend --pto 15
long-weekend --pto 15 --pto-year 2027=20     # a different budget next year
long-weekend --pto 15 --carryover 5          # unused days roll forward
```

### Floating holidays

Plenty of employers grant a few discretionary days on top of the fixed holiday
list — often to cover the holidays their published list leaves out. They are not
PTO: they are granted per calendar year, and they expire on December 31 rather
than rolling over.

```
long-weekend --pto 15 --floating 2
long-weekend --floating 3                    # floating days alone; no PTO
long-weekend --pto 15 --floating 2 --floating-year 2027=3
```

The planner schedules both pools together, since a day off is a day off, but it
spends **floating days first** — they are the ones that vanish at year end — and
the report keeps the two apart so you can see what you actually drew down:

```
2026   15 PTO day(s) + 2 floating holiday(s) available
...
  17 of 17 day(s) used in 2026 (15 PTO, 2 floating)
```

Carryover follows from the same rule: only unused *PTO* can roll forward, so a
year that ends with floating days on the table loses them no matter how generous
`--carryover` is.

The budget is **per calendar year**, which is how PTO normally works. Breaks are
still allowed to span New Year — a Christmas-to-January trip draws from both
years' allowances, and the plan says which days come from which.

### What it optimises for

It maximises the total number of days that land inside a break of at least
`--min-break` consecutive days off (4 by default). That values turning a
three-day weekend into a four-day one far above adding a day to an existing
nine-day break, which is how most people actually feel about time off.

The search is an exact dynamic program, not a greedy pass over bridge
candidates — greedy gets the easy cases right but loses whenever two
opportunities compete for the same budget. The test suite checks the result
against exhaustive search.

### Many long weekends, or fewer long trips

Once the obvious holiday bridges are bought, the leftover budget has to go
somewhere, and there is no universally right answer — so it is a flag:

```
long-weekend --pto 15 --style weekends    # default: as many long weekends as possible
long-weekend --pto 15 --style vacations   # consolidate leftovers into longer trips
```

With 15 days against the `corporate` preset in 2026, `weekends` produces
thirteen four-day breaks; `vacations` produces five four-day breaks plus three
nine-day ones. Raising `--min-break` is the blunter version of the same idea:
`--min-break 7` will not build anything shorter than a week.

### Real-world constraints

```
--blackout 2026-09-28:2026-10-09     # busy season; never schedule PTO here
--commit 2026-07-20:2026-07-24       # already booked; charge it to the budget
--max-consecutive-pto 5              # never take more than a week straight
```

`--blackout` and `--commit` are repeatable and accept single dates or
`START:END` ranges.

## Output formats

```
long-weekend --pto 15 --format text     # default
long-weekend --pto 15 --format json     # for scripting
long-weekend --pto 15 --format ics > timeoff.ics
```

The `.ics` file contains an all-day event for every holiday, every planned PTO
day, and every resulting break, so you can import the plan straight into a
calendar. Add `--no-holidays` to export only the PTO days and breaks.

## Using it as a library

```python
import datetime as dt
from longweekend import PRESETS, PtoBudget, build_days, optimize_pto, resolve_year

holidays = resolve_year(PRESETS["federal"], 2026)
days = build_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), holidays)
plan = optimize_pto(days, PtoBudget(default=15), min_break=4)

for item in plan.breaks:
    print(item.length, "days for", item.pto_cost, "PTO:", item.label())
```

## Development

```
pip install -e ".[dev]"
pytest
```

## Contributing

Contributions are welcome. If you find a bug or have a suggestion, please open
an issue or a pull request. New holiday presets for other countries or
industries are especially useful — add them to `PRESETS` in
`longweekend/catalog.py` with a test.
