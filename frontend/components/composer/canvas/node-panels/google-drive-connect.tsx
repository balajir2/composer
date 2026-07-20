"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { ComposerApiError } from "@/lib/api/client";
import {
  getGoogleDriveAuthorizeUrl,
  getPickerToken,
  listCloudStorageConnections,
  type CloudStorageConnection,
} from "@/lib/api/cloud-storage";

interface GoogleDocsView {
  setSelectFolderEnabled: (v: boolean) => GoogleDocsView;
  setIncludeFolders: (v: boolean) => GoogleDocsView;
}

interface GooglePicker {
  setVisible: (v: boolean) => void;
}

interface GooglePickerBuilder {
  addView: (view: GoogleDocsView) => GooglePickerBuilder;
  setOAuthToken: (token: string) => GooglePickerBuilder;
  setDeveloperKey: (key: string) => GooglePickerBuilder;
  setCallback: (
    cb: (data: { action: string; docs?: { id: string }[] }) => void
  ) => GooglePickerBuilder;
  build: () => GooglePicker;
}

declare global {
  interface Window {
    google?: {
      picker: {
        DocsView: new (viewId: unknown) => GoogleDocsView;
        ViewId: { FOLDERS: unknown };
        PickerBuilder: new () => GooglePickerBuilder;
        Action: { PICKED: string };
      };
    };
    gapi?: { load: (api: string, opts: { callback: () => void }) => void };
  }
}

function loadGooglePicker(): Promise<void> {
  return new Promise((resolve) => {
    if (window.google?.picker) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = "https://apis.google.com/js/api.js";
    script.onload = () => {
      window.gapi?.load("picker", { callback: () => resolve() });
    };
    document.body.appendChild(script);
  });
}

/** Which folder field a picker invocation is selecting for. Each maps to
 * its own node-data key so the three pickers (watch/processed/error) can
 * share the same Picker-opening logic below. */
type FolderKind = "watch" | "processed" | "error";

const FOLDER_KEY: Record<FolderKind, string> = {
  watch: "driveFolderId",
  processed: "driveProcessedFolderId",
  error: "driveErrorFolderId",
};

/**
 * Google Drive connect + folder-picker UI for the file-trigger node panel.
 *
 * Flow:
 *   1. "Connect Google Drive" opens the backend's authorize URL in a popup.
 *      The backend's /cloud-storage/google-drive/callback route exchanges
 *      the code, persists a CloudStorageConnection, then closes the popup
 *      after posting {type: "composer:google-drive-oauth", status} back to
 *      window.opener (see src/api/cloud_storage_oauth.py's
 *      _popup_close_html) — we listen for that message to know when to
 *      refresh the connection list.
 *   2. Once connected, "Select folder" fetches a one-time Picker access
 *      token from the backend (token never otherwise leaves the server)
 *      and opens the Google Picker UI scoped to folders only.
 *   3. Picking a folder writes {connectionId, driveFolderId} onto the node.
 *      The same picker also drives the optional Processed/Error folder
 *      fields (written to driveProcessedFolderId/driveErrorFolderId) —
 *      mirroring the local provider's destPath/errorPath, but as visible
 *      Drive folder moves rather than filesystem moves. Both are optional;
 *      leaving them unset keeps the original appProperties-marker-only
 *      behavior (see src/storage_providers/google_drive.py's move_file).
 */
export default function GoogleDriveConnect({
  connectionId,
  driveFolderId,
  driveProcessedFolderId,
  driveErrorFolderId,
  showProcessedErrorFolders = true,
  onChange,
}: {
  connectionId: string | undefined;
  driveFolderId: string | undefined;
  driveProcessedFolderId: string | undefined;
  driveErrorFolderId: string | undefined;
  /** file-trigger's claim-move destinations are meaningless on a write
   *  destination (e.g. download-pdf) -- defaults true so file-trigger's
   *  existing usage (which doesn't pass this prop) is unaffected. */
  showProcessedErrorFolders?: boolean;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const [connections, setConnections] = useState<CloudStorageConnection[]>([]);
  const [connecting, setConnecting] = useState(false);
  const [pickerBusy, setPickerBusy] = useState<FolderKind | null>(null);

  // Returns the freshly-fetched list (not just setting state) so callers —
  // notably the OAuth postMessage handler below — can act on the result
  // immediately instead of waiting on a re-render to see the new
  // connection show up in `connections`.
  async function refreshConnections(): Promise<CloudStorageConnection[]> {
    try {
      const list = await listCloudStorageConnections("google-drive");
      setConnections(list);
      return list;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load Google Drive connections.");
      return connections;
    }
  }

  useEffect(() => {
    refreshConnections();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    async function onMessage(event: MessageEvent) {
      if (event.data?.type !== "composer:google-drive-oauth") return;
      setConnecting(false);
      if (event.data.status === "success") {
        const list = await refreshConnections();
        // Don't clobber an already-selected connection (e.g. the user is
        // re-authorizing an existing node's connection after it expired).
        if (connectionId) return;
        // First-time connect with exactly one Google Drive connection on
        // the account — auto-select it so the user reaches "Select folder"
        // with zero extra clicks. With zero or 2+ connections (e.g. the
        // user already had other Drive accounts connected from other
        // nodes), leave it unset and let them pick explicitly via the
        // dropdown rendered below.
        if (list.length === 1) {
          const [only] = list;
          if (only) onChange({ connectionId: only.id });
        }
      } else {
        toast.error(event.data.detail || "Google Drive authorization failed.");
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionId]);

  const connected = connections.find((c) => c.id === connectionId);

  async function handleConnect() {
    setConnecting(true);
    try {
      const url = await getGoogleDriveAuthorizeUrl();
      const popup = window.open(url, "composer-google-drive-oauth", "width=500,height=650");
      if (!popup) {
        setConnecting(false);
        toast.error("Popup blocked — allow popups for this site and try again.");
      }
    } catch (err) {
      setConnecting(false);
      toast.error(err instanceof Error ? err.message : "Failed to start Google Drive authorization.");
    }
  }

  async function handlePickFolder(kind: FolderKind) {
    if (!connected) return;
    setPickerBusy(kind);
    try {
      const accessToken = await getPickerToken(connected.id);
      await loadGooglePicker();
      const google = window.google;
      if (!google) {
        toast.error("Google Picker failed to load.");
        return;
      }
      const view = new google.picker.DocsView(google.picker.ViewId.FOLDERS)
        .setSelectFolderEnabled(true)
        .setIncludeFolders(true);
      const picker = new google.picker.PickerBuilder()
        .addView(view)
        .setOAuthToken(accessToken)
        .setDeveloperKey(process.env.NEXT_PUBLIC_GOOGLE_PICKER_API_KEY ?? "")
        .setCallback((data) => {
          if (data.action === google.picker.Action.PICKED && data.docs?.[0]) {
            onChange({ connectionId: connected.id, [FOLDER_KEY[kind]]: data.docs[0].id });
          }
        })
        .build();
      picker.setVisible(true);
    } catch (err) {
      if (err instanceof ComposerApiError && err.status === 409) {
        // Backend's get_picker_token() returns 409 specifically when the
        // stored token is expired/unrefreshable (see
        // src/api/cloud_storage_oauth.py) — surface a reconnect prompt
        // rather than a generic error.
        toast.error("Your Google Drive connection expired — click Connect Google Drive to reconnect.");
      } else {
        toast.error(err instanceof Error ? err.message : "Failed to open the Google Drive folder picker.");
      }
    } finally {
      setPickerBusy(null);
    }
  }

  return (
    <div className="space-y-2">
      {!connected ? (
        <div className="space-y-2">
          <Button type="button" size="sm" disabled={connecting} onClick={handleConnect}>
            {connecting ? "Connecting…" : "Connect Google Drive"}
          </Button>
          {connections.length > 0 && (
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">
                Or use an already-connected Google account:
              </div>
              <NativeSelect
                id="ft-drive-connection"
                value=""
                onValueChange={(v) => onChange({ connectionId: v })}
                placeholder="Choose a connection…"
                options={connections.map((c) => ({ value: c.id, label: c.accountEmail }))}
              />
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <div className="text-xs text-muted-foreground">
              Connected as {connected.accountEmail}
              {driveFolderId ? ` — watching folder ${driveFolderId}` : " — no folder selected yet"}
            </div>
            <div className="mt-1 flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={pickerBusy !== null}
                onClick={() => handlePickFolder("watch")}
              >
                {pickerBusy === "watch" ? "Opening…" : driveFolderId ? "Change folder" : "Select folder"}
              </Button>
              {/* The connection row can exist (so `connected` is truthy) while
                  its stored token is expired/unrefreshable — the picker-token
                  fetch above then 409s with a "reconnect" toast. Keep a
                  reconnect path reachable even in the "connected" state so
                  that toast's instruction is actually actionable. */}
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={connecting}
                onClick={handleConnect}
                title="Re-run Google authorization for this account (use if the connection expired)"
              >
                {connecting ? "Connecting…" : "Reconnect"}
              </Button>
            </div>
          </div>

          {showProcessedErrorFolders && (
            <div className="space-y-1 border-t pt-2">
              <div className="text-xs text-muted-foreground">
                Processed folder (optional) — successfully-handled files are moved here
                {driveProcessedFolderId ? `: ${driveProcessedFolderId}` : ""}
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={pickerBusy !== null}
                onClick={() => handlePickFolder("processed")}
              >
                {pickerBusy === "processed"
                  ? "Opening…"
                  : driveProcessedFolderId
                    ? "Change folder"
                    : "Select folder"}
              </Button>
            </div>
          )}

          {showProcessedErrorFolders && (
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">
                Error folder (optional) — files that fail to process are moved here
                {driveErrorFolderId ? `: ${driveErrorFolderId}` : ""}
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={pickerBusy !== null}
                onClick={() => handlePickFolder("error")}
              >
                {pickerBusy === "error" ? "Opening…" : driveErrorFolderId ? "Change folder" : "Select folder"}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
