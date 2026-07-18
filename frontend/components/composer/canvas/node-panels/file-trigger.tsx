"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import GoogleDriveConnect from "./google-drive-connect";

export default function FileTriggerPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">
          Watch configuration
        </p>
        <p className="mb-3 text-[10px] text-muted-foreground">
          This node is visual-only — it does not run as part of the
          workflow. For Local filesystem, it configures the{" "}
          <code>composer watch</code> CLI, which polls the source folder and
          triggers this workflow when a new file is claimed. For Google
          Drive, Composer&apos;s own backend polls the connected account on a
          schedule — no local process needed. Publish this workflow
          (Production toggle) before either mechanism can trigger it.
        </p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="ft-provider" className="text-xs">
              Provider
            </Label>
            <NativeSelect
              id="ft-provider"
              value={(data.provider as string) ?? "local"}
              onValueChange={(v) => onChange({ provider: v })}
              options={[
                { value: "local", label: "Local filesystem" },
                { value: "google-drive", label: "Google Drive" },
              ]}
            />
          </div>
          {(data.provider as string) !== "google-drive" ? (
            <>
              <div className="space-y-1">
                <Label htmlFor="ft-source" className="text-xs">
                  Source path
                </Label>
                <Input
                  id="ft-source"
                  value={(data.sourcePath as string) ?? ""}
                  onChange={(e) => onChange({ sourcePath: e.target.value })}
                  placeholder="/watch/in"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ft-dest" className="text-xs">
                  Destination path (on successful claim)
                </Label>
                <Input
                  id="ft-dest"
                  value={(data.destPath as string) ?? ""}
                  onChange={(e) => onChange({ destPath: e.target.value })}
                  placeholder="/watch/done"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ft-error" className="text-xs">
                  Error path (on extraction/trigger failure)
                </Label>
                <Input
                  id="ft-error"
                  value={(data.errorPath as string) ?? ""}
                  onChange={(e) => onChange({ errorPath: e.target.value })}
                  placeholder="/watch/error"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ft-poll" className="text-xs">
                  Poll interval (seconds)
                </Label>
                <Input
                  id="ft-poll"
                  type="number"
                  min={1}
                  value={
                    typeof data.pollIntervalSeconds === "number"
                      ? String(data.pollIntervalSeconds)
                      : "30"
                  }
                  onChange={(e) => {
                    const n = parseInt(e.target.value, 10);
                    onChange({ pollIntervalSeconds: Number.isFinite(n) ? n : 30 });
                  }}
                  className="font-mono text-xs"
                />
              </div>
            </>
          ) : (
            <GoogleDriveConnect
              connectionId={data.connectionId as string | undefined}
              driveFolderId={data.driveFolderId as string | undefined}
              driveProcessedFolderId={data.driveProcessedFolderId as string | undefined}
              driveErrorFolderId={data.driveErrorFolderId as string | undefined}
              onChange={onChange}
            />
          )}
          <div className="space-y-1">
            <Label htmlFor="ft-target-var" className="text-xs">
              Target Start input variable
            </Label>
            <Input
              id="ft-target-var"
              value={(data.targetInputVariable as string) ?? ""}
              onChange={(e) => onChange({ targetInputVariable: e.target.value })}
              placeholder="requirements_doc"
              className="font-mono text-xs"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
