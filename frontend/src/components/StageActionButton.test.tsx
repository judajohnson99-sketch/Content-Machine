import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "../test/renderWithClient";
import { StageActionButton } from "./StageActionButton";
import { ApiError } from "../api/client";
import * as pipelineApi from "../api/pipeline";

describe("StageActionButton", () => {
  it("triggers the stage with a freshly generated client_request_id", async () => {
    const trigger = vi.spyOn(pipelineApi, "triggerStage").mockResolvedValue({
      id: 1, video_id: "abc", stage: "creative", client_request_id: "x",
      celery_task_id: null, status: "QUEUED", params: {}, exit_code: null,
      message: "", data: {}, log_tail: "", created_at: "", started_at: null,
      finished_at: null,
    });

    renderWithClient(<StageActionButton videoId="abc" stage="creative" label="Run" params={{ force: true }} />);
    await userEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(trigger).toHaveBeenCalledTimes(1));
    const [videoId, stage, clientRequestId, params] = trigger.mock.calls[0];
    expect(videoId).toBe("abc");
    expect(stage).toBe("creative");
    expect(clientRequestId).toMatch(/^[0-9a-f-]{36}$/);
    expect(params).toEqual({ force: true });
  });

  it("is disabled and shows the reason when the caller marks it disabled", () => {
    renderWithClient(
      <StageActionButton videoId="abc" stage="run" label="Run" disabled disabledReason="busy" />,
    );
    const button = screen.getByRole("button", { name: "Run" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "busy");
  });

  it("shows a transient busy notice on 409 without treating it as a hard error", async () => {
    vi.spyOn(pipelineApi, "triggerStage").mockRejectedValue(new ApiError(409, "busy"));

    renderWithClient(<StageActionButton videoId="abc" stage="run" label="Run" />);
    await userEvent.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already in progress/i);
  });

  it("shows the raw error message for a non-409 failure", async () => {
    vi.spyOn(pipelineApi, "triggerStage").mockRejectedValue(new ApiError(400, "force: not a valid boolean"));

    renderWithClient(<StageActionButton videoId="abc" stage="run" label="Run" />);
    await userEvent.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("force: not a valid boolean");
  });
});
