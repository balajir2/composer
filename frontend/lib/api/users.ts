import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type UserSearchResult = components["schemas"]["UserSearchResult"];

export async function searchUsers(query: string): Promise<UserSearchResult[]> {
  if (query.trim().length < 2) return [];
  const q = new URLSearchParams({ q: query.trim() });
  return apiFetch<UserSearchResult[]>(`/users/search?${q.toString()}`);
}
