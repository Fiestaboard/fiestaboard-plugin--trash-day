# Trash & Recycling Day Setup Guide

Put your collection schedule on the board so nobody has to remember whether this is a recycling week.

## Overview

This plugin doesn't look anything up online — you tell it your schedule once and it does the date maths
from then on. You describe up to four **streams** (Trash, Recycling, Compost, Yard Waste), each with a
collection day and a weekly or every-other-week cadence. The plugin shows the next pickup for each, and
can take over the board the evening before so the bins go out.

**Prerequisites:**
- Your collection day(s), from your city or hauler's website
- For every-other-week pickups, one date you know the bin was (or will be) collected
- No API key, no account, no internet connection required

## Quick Setup

### 1. Enable the Plugin

In the FiestaBoard web UI, go to **Integrations**, find **Trash & Recycling Day**, and click **Enable**.

### 2. Configure

Click **Configure** and fill in:

- **Collection Streams** — Click **Add** for each bin that gets collected (up to 4):
  - **Name** — A short label like `Trash`, `Recycling`, `Compost` or `Yard Waste`. Keep it to 12
    characters or less so it fits on a line.
  - **Collection Day** — The weekday the bin goes out.
  - **Frequency** — `Every week` or `Every other week`.
  - **Known Collection Date** — Only for every-other-week pickups. Enter any date you know that bin
    was collected, as `YYYY-MM-DD`. A past date works just as well as an upcoming one; it only tells
    the plugin which half of the fortnight is yours. It must fall on the same weekday you chose above.
  - **Color** — The tile color shown next to this stream on the default display.
- **Timezone** — Your local IANA timezone (e.g. `America/New_York`). This decides when "today" rolls
  over to "tomorrow".
- **Reminder Hour** — The hour of the evening (0–23, default `18`) from which the pickup counts as
  "tomorrow". Below this hour the day before, `{{trash_day.is_tomorrow}}` stays off.
- **Take Over the Board the Night Before** — Leave **off** to just use the variables in your own pages.
  Turn it **on** to have the board switch to a "bins out" reminder once per collection.
- **Refresh Interval** — How often the schedule is recomputed. The default of 900 seconds (15 min) is
  plenty; nothing changes faster than that.

### 3. Add a Template

Go to **Pages** and create a page using the Trash & Recycling Day plugin. A simple template:

```
{center}NEXT PICKUP
{center}{{next_weekday}} {{next_date}}

{center}{{next_streams}}
{center}{{message}}
```

Or skip this step entirely — with no template, the plugin renders its own page listing each stream with
a colored tile.

### 4. View Your Board

The next pickup appears on the board and updates itself as the days roll over.

---

## Working Out Your Schedule

### Weekly pickups

The common case. Pick the weekday and choose **Every week** — that's it.

### Every-other-week pickups

Recycling and yard waste are often fortnightly. The plugin needs to know *which* fortnight is yours,
so give it one **Known Collection Date**:

1. Find a date your bin was collected — last week's is fine, and so is the next one printed on your
   city's calendar.
2. Enter it as `YYYY-MM-DD` (e.g. `2026-09-01`).
3. Make sure it lands on the weekday you picked. If your recycling goes out on Tuesdays, the date must
   be a Tuesday, or the plugin will tell you it doesn't match.

From there, every date 14 days on from that anchor (in either direction) is a collection day.

### Where to find your schedule

- Your city or county's waste/sanitation page — most publish a lookup by street address
- The sticker on the bin itself
- The paper calendar your hauler mails out at the start of the year
- Many haulers offer a printable PDF calendar; the dates on it are exactly what you need for the anchor

---

## Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{next_date}}` | Date of the next collection day | `Sep 16` |
| `{{next_weekday}}` | Weekday of the next collection day | `Tuesday` |
| `{{days_until}}` | Days until the next collection (0 = today) | `3` |
| `{{is_today}}` | A collection happens today | `false` |
| `{{is_tomorrow}}` | A collection happens tomorrow, and it is past the Reminder Hour | `true` |
| `{{next_streams}}` | Streams going out on the next collection day | `Trash, Recycling` |
| `{{message}}` | One-line summary | `TRASH + RECYCLING TOMORROW` |
| `{{streams.0.name}}` | Name of the soonest stream | `Trash` |
| `{{streams.0.next_date}}` | Its next collection date | `Sep 16` |
| `{{streams.0.days_until}}` | Days until that stream | `3` |
| `{{streams.0.color}}` | Its configured color | `blue` |
| `{{streams.N.field}}` | Index into the streams array — `N` is 0-based, sorted soonest first | `{{streams.1.name}}` |

`days_until` comes with default color rules: red on collection day, yellow the day before, green
otherwise.

## Configuration Reference

| Setting | Description | Default | Range |
|---------|-------------|---------|-------|
| `streams` | Collection streams | one weekly Tuesday "Trash" | 1–4 entries |
| `streams[].name` | Short label | Required | up to 12 characters |
| `streams[].weekday` | Collection day | `Tuesday` | Monday–Sunday |
| `streams[].frequency` | Cadence | `weekly` | `weekly`, `biweekly` |
| `streams[].anchor_date` | Known collection date | Required for `biweekly` | `YYYY-MM-DD` on the chosen weekday |
| `streams[].color` | Tile color | `blue` | red, orange, yellow, green, blue, violet, white |
| `timezone` | IANA timezone name | `America/Los_Angeles` | any valid IANA zone |
| `reminder_hour` | Evening-before cutoff | `18` | 0–23 |
| `enable_triggers` | Take over the board the night before | `false` | true / false |
| `refresh_seconds` | Recompute interval | `900` | 300–86400 |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `TRASH_DAY_ENABLED` | Set to `true` to enable the plugin without using the UI |

---

## Troubleshooting

**"At least one collection stream is required"**
- Add at least one stream under **Collection Streams**. The plugin has nothing to show without one.

**"A known collection date (YYYY-MM-DD) is required for biweekly pickups"**
- Every-other-week streams need an anchor date so the plugin knows which weeks are yours. See
  "Every-other-week pickups" above.

**"Known collection date ... is not a Tuesday"**
- The anchor date must fall on the same weekday as the stream. Either fix the date, or change the
  stream's **Collection Day** to match it.

**The every-other-week date is a week off**
- The anchor is on the wrong week. Pick a date you're certain about — look at the bin or your hauler's
  calendar — and re-enter it. Any correct collection date works, past or future.

**The day flips over at the wrong time**
- Set **Timezone** to your own IANA zone. The plugin uses it to decide the current date, so a wrong
  zone can leave the board a day behind or ahead around midnight.

**`{{is_tomorrow}}` is false the day before collection**
- It only turns on from **Reminder Hour** onward (default 6pm). Lower it if you want the reminder
  earlier in the day.

**The board never takes over**
- Turn on **Take Over the Board the Night Before**. It is off by default.
- The takeover fires from **Reminder Hour** on the evening before, and only once per collection — if
  you already saw (or dismissed) it, it will not come back for that same date.

**A stream is missing from the board**
- The default display fits the header plus one line per stream. On a Note (3 rows) only the first
  stream fits. Build your own page if you need a different layout.
- A stream with an unrecognized weekday is skipped. Check its **Collection Day**.

**Names are getting cut off**
- Stream names are trimmed to 12 characters on a Flagship and less on a Note. Use short labels like
  `Recycling` rather than `Recycling & Glass`.
