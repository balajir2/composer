"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";

/**
 * Gamma-AI node panel.
 *
 * Field names match `GammaAiNodeData` (src/engine/workflow.py:358).
 * Pydantic uses `populate_by_name=True` so we save with the camelCase
 * aliases the backend serialises to.  The previous panel only exposed
 * `prompt` + `exportAs`, hiding the rest of Gamma's content controls
 * (format, textMode, numCards, textAmount, imageSource, language) and
 * incorrectly dropped the `web` export option that the schema accepts.
 */

const FORMAT_OPTIONS = [
  { value: "presentation", label: "Presentation" },
  { value: "document", label: "Document" },
  { value: "social", label: "Social" },
];

const TEXT_MODE_OPTIONS = [
  // From Gamma's API: how to handle the prompt text.
  // generate = expand outline into full content (most common)
  // condense = compress longer source text into slides
  // preserve = use prompt verbatim, no rewriting
  { value: "generate", label: "Generate (expand outline)" },
  { value: "condense", label: "Condense (summarise source)" },
  { value: "preserve", label: "Preserve (verbatim)" },
];

const TEXT_AMOUNT_OPTIONS = [
  { value: "", label: "Default (Gamma decides)" },
  { value: "brief", label: "Brief" },
  { value: "medium", label: "Medium" },
  { value: "detailed", label: "Detailed" },
];

const EXPORT_OPTIONS = [
  // Web returns a Gamma-hosted URL only; pptx/pdf trigger a render+download.
  { value: "web", label: "Web link only" },
  { value: "pptx", label: "PowerPoint (.pptx)" },
  { value: "pdf", label: "PDF" },
];

const IMAGE_SOURCE_OPTIONS = [
  { value: "", label: "Default (Gamma chooses)" },
  { value: "aiGenerated", label: "AI-generated" },
  { value: "pictographic", label: "Pictographic" },
  { value: "unsplash", label: "Unsplash" },
  { value: "webAllImages", label: "Web — all images" },
  { value: "webFreeToUse", label: "Web — free to use" },
  { value: "webFreeToUseCommercially", label: "Web — free for commercial use" },
  { value: "placeholder", label: "Placeholder" },
  { value: "noImages", label: "No images" },
];

export default function GammaAiPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const prompt = (data.prompt as string) ?? "";
  const format = (data.format as string) ?? "presentation";
  const textMode = (data.textMode as string) ?? "generate";
  const numCardsRaw = data.numCards;
  const numCards =
    typeof numCardsRaw === "number" ? String(numCardsRaw) : "";
  const textAmount = (data.textAmount as string) ?? "";
  const imageSource = (data.imageSource as string) ?? "";
  const language = (data.language as string) ?? "";
  const exportAs = (data.exportAs as string) ?? "web";

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="gamma-prompt">Prompt</Label>
        <Textarea
          id="gamma-prompt"
          value={prompt}
          onChange={(e) => onChange({ prompt: e.target.value })}
          rows={4}
          placeholder="Create a presentation about {{topic}}"
        />
        <p className="text-xs text-muted-foreground">
          Reference upstream variables with{" "}
          <code className="font-mono">&#123;&#123;name&#125;&#125;</code>.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="gamma-format">Format</Label>
          <NativeSelect
            id="gamma-format"
            value={format}
            onValueChange={(v) => onChange({ format: v })}
            options={FORMAT_OPTIONS}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="gamma-export">Export as</Label>
          <NativeSelect
            id="gamma-export"
            value={exportAs}
            onValueChange={(v) => onChange({ exportAs: v })}
            options={EXPORT_OPTIONS}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="gamma-text-mode">Text mode</Label>
        <NativeSelect
          id="gamma-text-mode"
          value={textMode}
          onValueChange={(v) => onChange({ textMode: v })}
          options={TEXT_MODE_OPTIONS}
        />
        <p className="text-xs text-muted-foreground">
          How Gamma treats your prompt text. Pick <strong>Preserve</strong>{" "}
          when the prompt already contains the slide-by-slide content;{" "}
          <strong>Generate</strong> when it&apos;s an outline or brief.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="gamma-num-cards">
            Number of cards / slides{" "}
            <span className="font-normal text-muted-foreground">(optional)</span>
          </Label>
          <Input
            id="gamma-num-cards"
            type="number"
            min={1}
            max={60}
            value={numCards}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "") {
                onChange({ numCards: undefined });
                return;
              }
              const n = parseInt(v, 10);
              onChange({ numCards: Number.isFinite(n) ? n : undefined });
            }}
            placeholder="leave blank to let Gamma decide"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="gamma-text-amount">Text amount per card</Label>
          <NativeSelect
            id="gamma-text-amount"
            value={textAmount}
            onValueChange={(v) => onChange({ textAmount: v || undefined })}
            options={TEXT_AMOUNT_OPTIONS}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="gamma-image-source">Image source</Label>
        <NativeSelect
          id="gamma-image-source"
          value={imageSource}
          onValueChange={(v) => onChange({ imageSource: v || undefined })}
          options={IMAGE_SOURCE_OPTIONS}
        />
        <p className="text-xs text-muted-foreground">
          For commercial decks, pick <strong>free to use commercially</strong>{" "}
          or <strong>AI-generated</strong> to avoid licensing surprises.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="gamma-language">
          Language{" "}
          <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Input
          id="gamma-language"
          value={language}
          onChange={(e) => onChange({ language: e.target.value || undefined })}
          placeholder="en, fr, es, ja, …"
        />
        <p className="text-xs text-muted-foreground">
          ISO language code. Leave blank to match the prompt&apos;s language.
        </p>
      </div>
    </div>
  );
}
