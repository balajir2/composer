import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type EvaluateTransformRequest = components["schemas"]["EvaluateTransformRequest"];
type EvaluateExpressionResult = components["schemas"]["EvaluateExpressionResult"];
type EvaluateDataTransformRequest = components["schemas"]["EvaluateDataTransformRequest"];
type EvaluateDataTransformResult = components["schemas"]["EvaluateDataTransformResult"];

export async function evaluateTransformExpression(
  body: EvaluateTransformRequest
): Promise<EvaluateExpressionResult> {
  return apiFetch<EvaluateExpressionResult>("/expressions/evaluate-transform", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function evaluateDataTransformExpression(
  body: EvaluateDataTransformRequest
): Promise<EvaluateDataTransformResult> {
  return apiFetch<EvaluateDataTransformResult>("/expressions/evaluate-data-transform", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
