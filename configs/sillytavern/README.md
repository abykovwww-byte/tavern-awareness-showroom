# SillyTavern Manual Setup

## NVIDIA API

Open SillyTavern and configure:

```text
API type: Chat Completion
Chat Completion Source: Custom (OpenAI-compatible)
Base URL: https://integrate.api.nvidia.com/v1
Model: z-ai/glm-5.2
```

Use the NVIDIA API key only in the SillyTavern UI.

## RP Profile

Copy these files into the appropriate SillyTavern fields:

- System Prompt: `configs/prompts/base-gm-system.md`;
- Author's Note: `configs/prompts/base-authors-note.md`;
- World Info/Lorebook template: `configs/world-info/world-template.md`;
- Character template: `configs/characters/character-template.md`;
- Generation reference: `configs/presets/openai-compatible-glm-5.2.json`.

