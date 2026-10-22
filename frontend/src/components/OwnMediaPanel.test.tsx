import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "../test/renderWithClient";
import { OwnMediaPanel } from "./OwnMediaPanel";
import * as libraryApi from "../api/library";
import type {
  LibraryAsset,
  LibraryListing,
  OwnerMediaRole,
  OwnerMediaSelection,
} from "../types/library";

const ROLES: OwnerMediaRole[] = ["visuals", "music", "ambience", "sfx"];

function selection(overrides: Partial<OwnerMediaSelection> = {}): OwnerMediaSelection {
  return {
    updated_utc: null,
    actor: null,
    roles: Object.fromEntries(
      ROLES.map((role) => [role, { label: role, count: 0, entries: [] }]),
    ) as unknown as OwnerMediaSelection["roles"],
    problems: [],
    stale_stages: [],
    ...overrides,
  };
}

function asset(overrides: Partial<LibraryAsset> = {}): LibraryAsset {
  return {
    id: "a".repeat(64),
    kind: "image",
    filename: "rain.png",
    origin: "owner",
    description: "Rain on a window at night",
    source: "Shot by the owner",
    tags: ["rain"],
    technical: {
      width: 1920, height: 1080, video_codec: "png", audio_codec: null,
      sample_rate: null, channels: null, duration_seconds: null, bytes: 2_097_152,
    },
    rights: {
      source: "Me", license: "My own work", commercial_use: true,
      attribution_required: false, attribution_text: null, evidence: "I shot it",
      status: null,
    },
  available: true,
  state: "ready",
    resolved_path: "/home/owner/rain.png",
    selectable: true,
    problems: [],
    roles: ["visuals"],
    ...overrides,
  };
}

function listing(assets: LibraryAsset[]): LibraryListing {
  return {
    assets,
    roles: ROLES.map((role) => ({
      role, label: role, kinds: role === "visuals" ? ["image", "video"] : ["audio"],
    })),
  };
}

describe("OwnMediaPanel", () => {
  it("says each role is generated until the owner chooses something", async () => {
    vi.spyOn(libraryApi, "getOwnerMedia").mockResolvedValue(selection());

    renderWithClient(<OwnMediaPanel videoId="abc" />);

    expect(await screen.findByRole("heading", { name: "Visuals" })).toBeInTheDocument();
    expect(screen.getByText(/Generated imagery is used\./)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Choose from library" })).toHaveLength(4);
  });

  it("chooses an asset for a role and saves it as an ordered list", async () => {
    vi.spyOn(libraryApi, "getOwnerMedia").mockResolvedValue(selection());
    vi.spyOn(libraryApi, "listLibrary").mockResolvedValue(listing([asset()]));
    const save = vi.spyOn(libraryApi, "setOwnerMedia").mockResolvedValue({
      selection: { updated_utc: null, actor: "owner", roles: selection().roles },
      changed_roles: ["visuals"],
      stale_stages: ["scenes"],
    });

    renderWithClient(<OwnMediaPanel videoId="abc" />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Choose from library" }))[0]);

    expect(await screen.findByText("Rain on a window at night")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Choose" }));
    await userEvent.click(screen.getByRole("button", { name: "Use this media" }));

    await waitFor(() => expect(save).toHaveBeenCalledWith("abc", { visuals: ["a".repeat(64)] }));
  });

  it("keeps unusable media out of the way but says how much there is and why", async () => {
    vi.spyOn(libraryApi, "getOwnerMedia").mockResolvedValue(selection());
    vi.spyOn(libraryApi, "listLibrary").mockResolvedValue(listing([
      asset({ selectable: false, rights: null, problems: ["needs a rights record"] }),
    ]));

    renderWithClient(<OwnMediaPanel videoId="abc" />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Choose from library" }))[0]);

    // Nothing is ready, and the one that is not says so by count first.
    expect(await screen.findByText(/nothing in your library is ready/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /1 more need details/i }));

    expect(await screen.findByText("needs a rights record")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Review details" })).toBeEnabled();
  });

  it("does not claim the current render uses media a stage has not run with", async () => {
    vi.spyOn(libraryApi, "getOwnerMedia").mockResolvedValue(
      selection({ stale_stages: ["scenes", "audio"] }),
    );

    renderWithClient(<OwnMediaPanel videoId="abc" />);

    expect(await screen.findByText(/the current render does not use this media yet/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /re-run scene images/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /re-run audio/i })).toBeInTheDocument();
  });

  it("raises a selected file whose bytes have changed as a blocker, not a hint", async () => {
    vi.spyOn(libraryApi, "getOwnerMedia").mockResolvedValue(
      selection({ problems: ["Visuals asset aaaaaaaaaaaa no longer matches the bytes that were selected"] }),
    );

    renderWithClient(<OwnMediaPanel videoId="abc" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/no longer matches/);
    expect(alert).toHaveTextContent(/Review is blocked/);
  });

  it("filters the library down to the kinds a role can take", async () => {
    vi.spyOn(libraryApi, "getOwnerMedia").mockResolvedValue(selection());
    vi.spyOn(libraryApi, "listLibrary").mockResolvedValue(listing([
      asset(),
      asset({ id: "b".repeat(64), kind: "audio", description: "A piano loop", roles: ["music"] }),
    ]));

    renderWithClient(<OwnMediaPanel videoId="abc" />);
    const buttons = await screen.findAllByRole("button", { name: "Choose from library" });
    await userEvent.click(buttons[1]);                       // Music

    expect(await screen.findByText("A piano loop")).toBeInTheDocument();
    expect(screen.queryByText("Rain on a window at night")).not.toBeInTheDocument();
  });

  it("previews selected audio from the staged project file", async () => {
    const audioEntry = {
      asset_id: "b".repeat(64), role: "music" as const, kind: "audio" as const,
      description: "A piano loop", origin: "owner", source: "Owner", rights: null,
      source_path: "/home/owner/piano.wav", staged_path: "owner-media/piano.wav",
      staged_by: "owner", technical: asset().technical, selected_utc: null,
      file: { path: "owner-media/piano.wav", bytes: 1000, modified_utc: "2026-10-07T00:00:00Z" },
    };
    const current = selection({
      roles: {
        ...selection().roles,
        music: { label: "Music", count: 1, entries: [audioEntry] },
      },
    });
    vi.spyOn(libraryApi, "getOwnerMedia").mockResolvedValue(current);

    renderWithClient(<OwnMediaPanel videoId="abc" />);

    await screen.findByText("A piano loop");
    const player = document.querySelector("audio");
    expect(player).not.toBeNull();
    expect(player).toHaveAttribute("src", expect.stringContaining("/projects/abc/files/owner-media/piano.wav"));
  });
});
