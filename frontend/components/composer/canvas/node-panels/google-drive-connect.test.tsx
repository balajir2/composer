import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import GoogleDriveConnect from "./google-drive-connect";

const { listCloudStorageConnections, getPickerToken, getGoogleDriveAuthorizeUrl, MockComposerApiError } =
  vi.hoisted(() => {
    // Mocked so the test doesn't need a real next-auth session to import
    // lib/api/client.ts transitively — only `ComposerApiError` is used by
    // the component, and the component's `err instanceof ComposerApiError`
    // check only works if both the component and the test import the SAME
    // (mocked) class, hence mocking here rather than importing the real
    // module.
    class MockComposerApiError extends Error {
      status: number;
      detail: unknown;
      constructor(status: number, detail: unknown) {
        super(`mock ${status}`);
        this.status = status;
        this.detail = detail;
      }
    }
    return {
      listCloudStorageConnections: vi.fn(),
      getPickerToken: vi.fn(),
      getGoogleDriveAuthorizeUrl: vi.fn(),
      MockComposerApiError,
    };
  });

vi.mock("@/lib/api/cloud-storage", () => ({
  listCloudStorageConnections,
  getPickerToken,
  getGoogleDriveAuthorizeUrl,
}));

vi.mock("@/lib/api/client", () => ({ ComposerApiError: MockComposerApiError }));

const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { error: (...args: unknown[]) => toastError(...args) } }));

function dispatchOauthMessage(status: "success" | "error", detail = "") {
  act(() => {
    window.dispatchEvent(
      new MessageEvent("message", { data: { type: "composer:google-drive-oauth", status, detail } })
    );
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  listCloudStorageConnections.mockResolvedValue([]);
});

describe("GoogleDriveConnect", () => {
  it("shows the Connect button with no connections and no connectionId", async () => {
    render(<GoogleDriveConnect connectionId={undefined} driveFolderId={undefined} onChange={vi.fn()} />);
    expect(await screen.findByText("Connect Google Drive")).toBeInTheDocument();
    expect(screen.queryByText(/Select folder/)).not.toBeInTheDocument();
  });

  it(
    "auto-selects the connection when OAuth succeeds and exactly one connection exists " +
      "(the blocking-bug scenario: a brand-new node reaching 'Select folder' after first connect)",
    async () => {
      listCloudStorageConnections.mockResolvedValue([
        { id: "conn-1", provider: "google-drive", accountEmail: "user@gmail.com" },
      ]);
      const onChange = vi.fn();
      render(<GoogleDriveConnect connectionId={undefined} driveFolderId={undefined} onChange={onChange} />);

      // Initial mount fetch resolves to the same single connection — but
      // connectionId is still undefined, so the component must NOT have
      // auto-selected yet (only the postMessage handler does that).
      await waitFor(() => expect(listCloudStorageConnections).toHaveBeenCalled());
      expect(onChange).not.toHaveBeenCalled();

      dispatchOauthMessage("success");

      await waitFor(() => expect(onChange).toHaveBeenCalledWith({ connectionId: "conn-1" }));
    }
  );

  it("does not auto-select when 2+ connections exist and none is selected — renders a picker dropdown instead", async () => {
    listCloudStorageConnections.mockResolvedValue([
      { id: "conn-1", provider: "google-drive", accountEmail: "a@gmail.com" },
      { id: "conn-2", provider: "google-drive", accountEmail: "b@gmail.com" },
    ]);
    const onChange = vi.fn();
    render(<GoogleDriveConnect connectionId={undefined} driveFolderId={undefined} onChange={onChange} />);

    await waitFor(() => expect(listCloudStorageConnections).toHaveBeenCalled());

    dispatchOauthMessage("success");
    // Give the async postMessage handler a tick to run its refresh + decide
    // not to auto-select.
    await waitFor(() => expect(listCloudStorageConnections).toHaveBeenCalledTimes(2));
    expect(onChange).not.toHaveBeenCalled();

    // The dropdown lets the user pick one of the pre-existing connections
    // explicitly (covers a brand-new node when other nodes already
    // connected Drive accounts).
    const combo = await screen.findByRole("combobox");
    fireEvent.change(combo, { target: { value: "conn-2" } });
    expect(onChange).toHaveBeenCalledWith({ connectionId: "conn-2" });
  });

  it("does not overwrite an already-selected connectionId on a re-authorize", async () => {
    listCloudStorageConnections.mockResolvedValue([
      { id: "conn-1", provider: "google-drive", accountEmail: "user@gmail.com" },
    ]);
    const onChange = vi.fn();
    render(<GoogleDriveConnect connectionId="conn-1" driveFolderId={undefined} onChange={onChange} />);

    await waitFor(() => expect(listCloudStorageConnections).toHaveBeenCalled());
    expect(await screen.findByText(/Connected as user@gmail.com/)).toBeInTheDocument();

    dispatchOauthMessage("success");
    await waitFor(() => expect(listCloudStorageConnections).toHaveBeenCalledTimes(2));

    // connectionId was already set — the handler must leave it alone.
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows a reconnect-specific message on a 409 from the picker-token endpoint", async () => {
    listCloudStorageConnections.mockResolvedValue([
      { id: "conn-1", provider: "google-drive", accountEmail: "user@gmail.com" },
    ]);
    getPickerToken.mockRejectedValue(new MockComposerApiError(409, "expired"));
    const onChange = vi.fn();
    render(<GoogleDriveConnect connectionId="conn-1" driveFolderId={undefined} onChange={onChange} />);

    const pickButton = await screen.findByText("Select folder");
    fireEvent.click(pickButton);

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/reconnect/i))
    );
  });

  it("renders a Reconnect affordance even while already connected, so the 409 toast's instruction is actionable", async () => {
    listCloudStorageConnections.mockResolvedValue([
      { id: "conn-1", provider: "google-drive", accountEmail: "user@gmail.com" },
    ]);
    render(<GoogleDriveConnect connectionId="conn-1" driveFolderId={undefined} onChange={vi.fn()} />);
    expect(await screen.findByText("Reconnect")).toBeInTheDocument();
  });
});
