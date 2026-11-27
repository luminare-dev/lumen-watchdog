# 0007. Components V2 for Discord, JSON publish for ntfy

- **Status:** accepted
- **Date:** 2026-11-27

## Context

Alerts are read on a phone, often only in the notification preview. They must show at a glance
how bad it is, what is wrong and where to go next, and one run must not flood a channel.

## Considered options

1. Discord embeds; ntfy with HTTP headers (`Title`, `Priority`, `Tags`).
2. Discord Components V2 (message flag `1 << 15`); ntfy JSON publishing to the server root.

## Decision

Chosen option: 2.

- **Discord**: a Container whose accent colour follows the severity, a Section with the header
  and a Thumbnail, one Text Display per group (new, worse, events, still open, recovered), a
  Separator, a footer with a `<t:…:f>` timestamp (shown in each reader's time zone) and an Action
  Row of link buttons. Components V2 forbids `content` and `embeds`, so the mention is a Text
  Display of its own above the container, and `allowed_mentions` lists only that user. A webhook
  that no application owns may send these non-interactive components only with
  `?with_components=true` on the execute URL. The renderer stays below 40 components and 4000
  characters of text and adds "… and N more" when it has to cut.
- **ntfy**: JSON publishing carries UTF-8 titles and up to three view actions without header
  encoding problems.

Both channels get at most one message per run. Rate limits (HTTP 429) are honoured with the
`retry_after` value from the body or the `Retry-After` header; server errors back off.

## Consequences

- Good: compact, colour-coded messages; snapshot tests pin the exact payloads.
- Bad: older Discord clients that do not support Components V2 show an empty message; the ntfy
  push stays the fallback.
