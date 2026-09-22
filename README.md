# Claude

## production/ — TikTok video pipeline (Team Claude)

Provider-agnostic pipeline: Creative JSON -> VideoProvider -> TTS -> Subtitles/Edit -> QA -> MP4.
See [`production/README.md`](production/README.md) for architecture, provider status, and how to run
`python -m production.cli render CLAUDE-D01`.