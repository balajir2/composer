import { apiFetch } from "./client";

export interface CloudStorageConnection {
  id: string;
  provider: string;
  accountEmail: string;
}

export async function getGoogleDriveAuthorizeUrl(): Promise<string> {
  const res = await apiFetch<{ authorizeUrl: string }>(
    "/cloud-storage/google-drive/authorize"
  );
  return res.authorizeUrl;
}

export async function listCloudStorageConnections(
  provider: string
): Promise<CloudStorageConnection[]> {
  return apiFetch<CloudStorageConnection[]>(
    `/cloud-storage/connections?provider=${encodeURIComponent(provider)}`
  );
}

export async function getPickerToken(connectionId: string): Promise<string> {
  const res = await apiFetch<{ accessToken: string }>(
    `/cloud-storage/connections/${encodeURIComponent(connectionId)}/picker-token`,
    { method: "POST" }
  );
  return res.accessToken;
}
