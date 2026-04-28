import { getSession } from "next-auth/react";

import { ComposerApiError } from "./client";

const baseUrl =
  process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

/**
 * Result of POST /uploads/extract-text.  The `text` field is what
 * downstream code stashes as the input variable's value — engine
 * sees it as a regular string, no special handling required.
 */
export interface ExtractDocumentTextResponse {
  filename: string;
  content_type: string;
  size_bytes: number;
  text: string;
}

type Session = {
  accessToken?: string;
} | null;

/**
 * Upload a single file (PDF / DOCX / TXT / MD) and get back the
 * extracted plain text.  The endpoint is rate-limited per-user
 * (20/min) and capped at 10 MB; the form layer surfaces 4xx
 * messages directly so designers see clear errors when they pick
 * unsupported formats or oversized files.
 *
 * Why we don't use `apiFetch`: that helper assumes JSON request
 * bodies, but multipart upload needs the FormData object passed
 * raw with NO Content-Type header so the browser fills in the
 * boundary.  Setting Content-Type ourselves breaks the upload.
 */
export async function extractDocumentText(
  file: File
): Promise<ExtractDocumentTextResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const session = (await getSession()) as Session;
  const headers = new Headers();
  if (session?.accessToken) {
    headers.set("Authorization", `Bearer ${session.accessToken}`);
  }

  const response = await fetch(`${baseUrl}/uploads/extract-text`, {
    method: "POST",
    headers,
    body: formData,
  });

  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      /* non-JSON error body — keep null and let ComposerApiError fall back */
    }
    throw new ComposerApiError(response.status, detail);
  }

  return (await response.json()) as ExtractDocumentTextResponse;
}
