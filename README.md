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

It also knows the days you take off yourself — Jewish holidays off a real
Hebrew calendar, security conferences off a catalog you can extend — and plans
the rest of your leave around them.

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

### How much PTO is worth spending

The planner will happily spend the whole budget, even when the last days buy
little. These options measure that and let you stop earlier. They all need
`--pto` as the ceiling.

```
long-weekend --pto 17 --efficiency            # score the plan
long-weekend --pto 17 --curve                 # budget against days in breaks
long-weekend --pto 17 --min-return 2.5        # stop when a day returns under 2.5
long-weekend --pto 17 --target-days-off 40    # the fewest PTO days that reach 40
```

**Days off** here means *days in breaks*: days that fall inside a break of at
least `--min-break` consecutive days off. Plain weekends and stray holidays do
not count. The **efficiency** is days in breaks per PTO day spent, overall and
for each break. (The older "days off per PTO day spent" line in the totals
counts every day off in the window, weekends included, so it is a larger
number. Whenever the new score is printed beside it, that older line is
labeled "every day off, weekends included" and the new one "days in breaks
only", so the two cannot be confused.) `--efficiency` adds the score to the text
output, to the JSON (`plan.efficiency`) and to the description of each break
event in the `.ics` file. Without any of these options the output is exactly what
it was before.

**The return curve** replans every budget from 0 up to `--pto` and records
what each buys. A budget `b` gives every year `min(its grant, b)` PTO days;
floating holidays, carryover, `--commit` and `--blackout` stay as configured.
The **knee** is the last budget that still earns the best rate of return, the
point just before the first step that earns less. For the federal preset in
2027 with `--min-break 4`:

```
$ long-weekend --start-year 2027 --years 1 --min-break 4 --pto 17 --curve
  Budget  PTO spent  Days in breaks  Last day returned  Days per PTO
       0          0               0                  -             -
       1          1               4                4.0           4.0
      ...
      10         10              40                4.0           4.0
      11         11              44                4.0           4.0  <- knee
      12         12              45                1.0           3.8
      ...
      17         17              56                3.0           3.3
```

The first 11 days return 4 days each; the last six return about 2 each, and
17 PTO days give 56 days in breaks (the "about 55" figure that gets quoted).
With `--format json` the same rows appear under `curve.points`; in Python, call
`longweekend.efficiency.return_curve(days, budget, min_break=4)` and use
`curve.as_dicts()`, `curve.knee` and `curve.points`.

**`--min-return X`** stops spending once extra days would return fewer than
`X` days in breaks each, on average. It does not judge one day at a time,
because the curve is lumpy: the first of two days that bridge a midweek holiday
can return nothing, and the federal tail alternates between 1 and 3. Instead it
takes the *upper hull* of the curve, the best average any mix of budgets can
reach (picture a string pulled tight over the top of the points). Moving
along the hull, each stretch has a return per PTO day, and those only fall. The
plan stops at the last point reached by a stretch returning at least `X`. A
lone zero-return day therefore no longer stops the plan, and a poor day
followed by a good one is judged together. For the federal preset in 2027
with `--min-break 4`: `--min-return 3.5` or `2.5` stops at 11 days (44 days
in breaks), and `1.9` spends all 17 (56). When every stretch clears `X`, the
output says it used the full budget and that the extra days averaged at least
`X` (an individual day can still return less). The stopping budget is one per-year cap
shared by every year in the window, never above a year's own grant; the output
says "up to N PTO days per year" for that reason.

**`--target-days-off N`** (also spelled `--target-days-in-breaks`) takes the
smallest per-year budget that reaches `N` days in breaks: `--target-days-off 40`
plans 10 PTO days. If `--pto` cannot reach `N`, it exits with an error that
names the most it can reach (here, 56). With several years the cap is shared,
so the total is the fewest for that shape, not a global minimum across uneven
years.

Each budget is a full exact plan, so `--curve` and the stop options take longer
as `--pto` grows. Budgets past the working days in a year are not replanned
(they cannot change anything), but a ceiling in the hundreds across several
years still takes about a minute.

`--min-return` and `--target-days-off` are alternatives; both also accept
`min_return` and `target_days_off` in a config file.

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

## Employer calendars, honest counting and goals

Three things for planning against one employer's real calendar.

### An employer's paid days, with a confirmation status

List the paid days as explicit dates and say how sure you are of each. See
[`examples/employer-2027.yaml`](examples/employer-2027.yaml) (and the JSON twin
[`examples/employer-2027.json`](examples/employer-2027.json)):

```yaml
observance: none      # these are already the observed dates; do not shift them
holidays:
  - {name: "Memorial Day",                date: 2027-05-31, status: assumed}
  - {name: "Independence Day (observed)", date: 2027-07-05, status: observed-unconfirmed}
  - {name: "Thanksgiving",                date: 2027-11-25, status: confirmed}
```

`status` is `confirmed`, `assumed` (believed to be paid, not confirmed) or
`observed-unconfirmed` (the holiday is paid; the observed date is a guess). It
works on entries with an explicit `date`. The example has exactly eight days
and nothing else is added: no MLK, Presidents, Juneteenth, Columbus, Veterans,
Good Friday, Christmas Eve or extra New Year's Eve. Turn extras on by hand with
`--add`, or add floating days with `--floating`. Two holidays on one date count
once.

If any day in the window is not confirmed, the result is labeled
**PROVISIONAL** in text, JSON (`"provisional": true` plus `"assumptions"`) and
`.ics` (`X-LWP-PROVISIONAL`, and in each PTO, break and holiday description),
and the unconfirmed days are listed.

```
long-weekend --config examples/employer-2027.yaml --start-year 2027 --years 1
```

### How days off are counted

The headline number follows fixed rules (the code is `longweekend/honest.py`,
plain calendar-date arithmetic, no clocks or time zones):

* A **break** is a maximal run of nonworking days: weekends, holidays and PTO.
  Breaks that touch or overlap merge.
* The **featured total** counts only breaks that contain at least one PTO day,
  each calendar date once. A holiday weekend that costs nothing is reported
  separately, never added to the headline.
* PTO is charged only on a scheduled workday that is not a holiday. A holiday
  that lands on a weekend costs nothing and is counted once, as a weekend day.
* The window reaches past the selected year, so a break crossing December 31
  is whole (Dec 24, 2027 to Jan 2, 2028 is one 10-day break).
* Budgets are per calendar year: PTO is charged to the year it is taken.
* The report gives PTO used, holidays, weekend days, number of breaks, longest
  break, days inside the selected year versus adjacent years, and all annual
  nonworking days separately.

### Goals

Pass any of these and the planner scores by the rules above instead of the
classic days-in-breaks score. They need a PTO budget.

```
long-weekend --config examples/employer-2027.yaml --start-year 2027 --years 1 --goal weekends
long-weekend --config examples/employer-2027.yaml --start-year 2027 --years 1 --goal vacations --min-vacation-days 9
long-weekend --config examples/employer-2027.yaml --start-year 2027 --years 1 --goal balanced --max-breaks 8
long-weekend --config examples/employer-2027.yaml --start-year 2027 --years 1 --goal weekends --prefer 2027-08-13 --blackout 2027-03-01:2027-03-12
```

| Goal | What a PTO break of `L` days is worth |
| --- | --- |
| `weekends` | `L` if `L >= 3` (override the 3 with `--min-break`) |
| `vacations` | `L` if `L >= V`, else 0 (`V` is `--min-vacation-days`, default 7) |
| `balanced` | `L` if `L >= 3`, plus another `L` if `L >= V`: a vacation day counts double |

A plan's score is the sum over its breaks. `--prefer DATE[:DATE]` adds 2 points
for each preferred date taken as PTO (enough to break ties and tip close calls,
not to buy a day that does nothing). `--blackout` works as before.
`--max-breaks N` caps the number of breaks that contain PTO. The search is the
same exact dynamic program as the classic planner with a break counter added,
so the cap is exact; the output says so. `--min-vacation-days` alone means
`--goal vacations`; the other options alone mean `--goal weekends`. Goals do
not combine with `--curve`, `--efficiency`, `--min-return`, `--target-days-off`
or `--style`; they are rejected with a message, since the goal output already
reports the honest count. A 5-year window with a high `--max-breaks` can take
tens of seconds.
All of these can also be set in a config file (`goal`, `min_vacation_days`,
`prefer`, `max_breaks`).

Every goal run also plans the other two goals under the same constraints and
prints a **Tradeoff** table. The largest total is not marked as best: many
short breaks and a few long ones can cost the same PTO and give different
totals and a different kind of rest.

On the example employer file for 2027 with 17 PTO (the commands above, with
`--start-year 2027 --years 1`; without a window the tool plans this year and
next and the budget applies to each, so the numbers differ; they also move if
an unconfirmed day moves):

| Goal | Breaks | Days off | Longest |
| --- | ---: | ---: | ---: |
| `weekends` | 17 | 59 | 5 |
| `weekends --max-breaks 14` | 14 | 55 | 10 |
| `balanced` | 6 | 45 | 10 |
| `vacations` | 4 | 39 | 12 |

### Validation cases

Two PTO sets are scored by the counting function as exact tests. They are
**validation cases, not proven optima**: the planner is not required to
reproduce them, and `--goal weekends` finds a plan with more days than A.

* **A**, 17 PTO: Jan 4, May 28, Jul 2, Sep 3, Nov 24, Dec 27-30, and Fridays
  Feb 5, Mar 5, Apr 9, May 7, Jun 4, Aug 6, Oct 1, Nov 5 (2027). 14 breaks, 55
  days = 17 PTO + 8 holidays + 30 weekend days; 53 in 2027, 2 in 2028; longest
  10. Break lengths 4, 3, 3, 3, 3, 4, 3, 4, 3, 4, 3, 3, 5, 10.
* **B**, 17 PTO: Jan 4, Jun 1-4, Jul 6-9, Sep 3, Nov 22-24, Dec 27-30. 6 breaks
  (4, 9, 9, 4, 9, 10), 45 days = 17 + 8 + 20; 43 in 2027, 2 in 2028; longest 10.
* **Bridge**: Nov 22-24 together with Dec 27-30 is 7 PTO for two breaks of 19
  calendar days in total. The report calls this out whenever a plan has it.

To score your own set of dates, commit them in full and let the planner have
no choice:

```
long-weekend --config examples/employer-2027.yaml --start-year 2027 --years 1 --goal weekends --commit 2027-01-04 --commit 2027-12-27:2027-12-30
```

### Assumptions awaiting confirmation

* Memorial Day (May 31) and Labor Day (Sep 6) are assumed paid days.
* The three observed dates are unconfirmed: Independence Day on Jul 5,
  Christmas on Dec 24, New Year's 2028 on Dec 31.
* 17 PTO days is only an example budget.

## Days you take off yourself

Company holidays are free: the office is shut and your balance is untouched.
The days *you* take are not. Tell the planner about them and two things happen:
the arithmetic gets honest, and the planner starts building around them instead
of ignoring them.

```
long-weekend --pto 15 --observe jewish
long-weekend --pto 15 --observe "Yom Kippur" --observe "Passover"
long-weekend --pto 15 --observe 2026-07-20        # any date you already know
```

Each one is charged to your budget unless it lands on a weekend or a day the
company already closes — and the report says which:

```
  Days you are taking yourself
    Thu Apr 02  Pesach                         1 day of leave
    Fri Apr 03  Pesach (day 2)                 1 day of leave   (diaspora second day)
    Sat Sep 12  Rosh Hashanah                  free, non-working day
    Mon Sep 21  Yom Kippur                     1 day of leave
```

Because they are committed rather than optional, the planner bridges *from*
them. Yom Kippur 2026 is a Monday, so the weekend before it is already yours;
the planner sees the 3-day break sitting there and buys the Tuesday to make it
four.

### The Hebrew calendar

Jewish holidays are fixed on the Hebrew calendar and wander across the
Gregorian one, so they are computed rather than tabulated — a table goes stale,
an algorithm does not. The full rule-based calendar is in
[`longweekend/hebrew.py`](longweekend/hebrew.py): leap years, the molad, the
postponement rules, and conversion in both directions, in the standard library
alone.

| Set | What it books |
| --- | --- |
| `jewish` | Yom Kippur, Rosh Hashanah and the yom tov days work is prohibited on |
| `jewish-all` | the above plus Chol HaMoed, Hanukkah, Purim, Tu BiShvat, Lag BaOmer, Tisha B'Av |
| `jewish-high-holidays` | Rosh Hashanah, Yom Kippur, Shemini Atzeret, Simchat Torah |

Naming a festival books the days work is actually prohibited, not the whole
span: `--observe "Passover"` takes the four yom tov days and leaves Chol HaMoed
alone. Ask for `jewish-all` if you want every day of it. Names are matched
loosely, so `Passover`, `Pesach`, `Chanukah` and `Succot` all land where you
would expect.

Two flags for the details that matter:

```
long-weekend --observe jewish --israel   # one festival day, not two
long-weekend --observe jewish --erev     # also take the day it begins at sunset
```

A Hebrew day starts at sunset, so every holiday actually begins the evening
before the date shown. `--erev` books that day too — and only for the *first*
day of a festival, since the evening before day two is day one, which you have
already booked.

### Security conferences

```
long-weekend --list-conferences
long-weekend --pto 15 --conference "DEF CON" --conference-travel 1
long-weekend --pto 20 --conference defcon --conference ccc
```

The bundled catalog knows DEF CON, Black Hat USA and Europe, BSides Las Vegas,
RSAC and the Chaos Communication Congress, by name or by the abbreviation you
would actually type. Announced dates are reported as-is; years with nothing
announced fall back to a recurrence rule and are clearly labelled
`(estimated)`, because organisers move these:

```
  DEF CON
      2026  Thu Aug 06 - Sun Aug 09, Las Vegas, NV
      2027  Thu Aug 05 - Sun Aug 08, Las Vegas, NV  (estimated)
```

Where no rule fits — RSAC wanders around the spring — the tool says nothing
rather than inventing a date. Anything bundled will go stale eventually, so
bring your own:

```
long-weekend --conferences my-cons.json --conference "My Local Con"
```

Same format as [the bundled file](longweekend/data/conferences.json); your
entries win on a name clash.

If your employer pays for the trip, it is not your leave — use `--blackout` on
those dates instead, so the planner keeps its hands off them without charging
you.

## Not sure where to start

```
long-weekend --interactive
```

Seven questions, then the plan — and the command line that would have produced
it, so you never have to answer them again:

```
Next time, skip the questions:

    long-weekend --pto 20 --floating 2 --observe jewish --conference "DEF CON"
```

## Output formats

```
long-weekend --pto 15 --format text     # default
long-weekend --pto 15 --format json     # for scripting
long-weekend --pto 15 --format ics > timeoff.ics
```

The `.ics` file contains an all-day event for every holiday, every planned PTO
day, and every resulting break, so you can import the plan straight into a
calendar. Add `--no-holidays` to export only the PTO days and breaks.

Note: event UIDs in `.ics` exports changed once (SHA-1 to SHA-256); re-importing an older export will not match the new events.

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
