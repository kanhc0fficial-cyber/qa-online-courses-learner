# First-version architecture

This fork uses `byjlw/video-analyzer` as the analysis application and ports the
low-cost preprocessing strategy from `mcp-video-analyzer`.

## Runtime flow

```text
local video
  -> Whisper transcript
  -> FFmpeg scene-change timestamps + uniform coverage samples
  -> black-frame filtering + dHash near-duplicate filtering
  -> chronological groups of up to 6 images
  -> structured multimodal scene records
  -> local bounding-box crops
  -> record.json + transcript.json + record.md
```

Frames detected as scene changes are never removed by dHash. This is important
because dHash intentionally ignores some global color and brightness changes.
Uniform candidates remain available as a fallback for talking-head videos,
slides, subtitles, and long stretches without cuts.

## Output layout

```text
output/
├─ record.md
├─ record.json
├─ transcript.json             # when audio transcription succeeds
└─ assets/
   ├─ frames/
   │  ├─ scene_001.jpg
   │  └─ scene_002.jpg
   └─ objects/
      └─ scene_001_1_phone.jpg
```

`record.json` stores chronological `scenes`. Each scene separates directly
visible facts, speech aligned from ASR, screen text, changes, key objects,
uncertainties, and inferences. No secondary summary is generated.

## Deliberately deferred

- OCR-aware deduplication: the schema and screen-text field are ready, but a
  local OCR engine has not yet been added as a mandatory Windows dependency.
- Transparent-background segmentation: version one creates padded rectangular
  crops. Grounded SAM-style segmentation should remain opt-in.
- API response caching and resumable stages.
