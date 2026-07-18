# Liquid Glass Application Branding Design

## Goal

Give Interview Assistant an original, recognizable Windows application icon and
installer icon based on the approved “Focus Spark / Prismatic Edge” direction.
The branding must remain legible at taskbar and shortcut sizes and must not copy
the shape, palette, or trade dress of another AI product.

## Approved Visual Direction

- Use a square black outer field with no text, letters, or wordmark.
- Center a dark rounded-square liquid-glass panel with visible depth and soft
  specular reflections.
- Place the original Focus Spark mark inside the panel: four rounded prismatic
  petals arranged around a small four-point center spark.
- Use neutral graphite, smoke, and soft white glass tones. The only saturated
  accent is emerald `#50DE73`, appearing as refracted edge light rather than a
  flat fill.
- Favor a bold silhouette and controlled highlights so the mark survives
  reduction to 16–32 pixels. Fine decorative detail is subordinate to clarity.
- Avoid typography, gradients associated with third-party AI brands, excessive
  neon glow, photographic scenery, borders around the outer canvas, and
  watermark-like details.

## Assets

Create project-owned branding files under `assets/branding/`:

- `interview-assistant-logo.png`: square high-resolution RGB/RGBA master used
  for inspection and future exports.
- `interview-assistant.ico`: Windows multi-resolution icon containing at least
  16, 24, 32, 48, 64, 128, and 256 pixel representations.

The PNG is generated as original artwork, then inspected before deterministic
ICO conversion. Downscaled icon frames use high-quality resampling and retain
the black field rather than introducing transparency.

## Application Integration

- Configure the PyInstaller EXE target to embed `interview-assistant.ico` as the
  executable resource. The installed executable, Start Menu shortcut, desktop
  shortcut, taskbar entry, and uninstall display icon therefore share one mark.
- Include the branding assets in the frozen distribution only where required by
  runtime code or packaging; do not add unrelated branding files.
- Set the Qt application/window icon explicitly when needed so source launches
  and packaged launches show the same icon.
- Configure Inno Setup `SetupIconFile` to use the same ICO, giving the setup
  executable the approved icon as well.

## Release Boundaries

The branding change does not alter the release contents policy. The installer
still contains only the application runtime, its required CUDA/cuDNN libraries,
the manifest-pinned offline STT model, and the small branding asset. LM Studio,
LLM weights, LM Link, MCP servers/configuration, API tokens, and user settings
remain excluded.

## Verification

- Validate the PNG dimensions and absence of unexpected embedded text metadata.
- Validate all required ICO frame sizes with Pillow.
- Add focused packaging tests for the PyInstaller and Inno Setup icon wiring.
- Run static checks and the full pytest suite.
- Rebuild the STT-inclusive onedir distribution and installer from committed
  source.
- Repeat the isolated silent install, frozen diagnostics, bundled-STT manifest
  validation, and silent uninstall checks.
- Inspect the final setup and source archive inventories and regenerate SHA-256
  checksums only after the branded artifacts pass smoke testing.
