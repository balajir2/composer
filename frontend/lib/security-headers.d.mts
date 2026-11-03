export function buildSecurityHeaders(env?: { nodeEnv?: string; apiUrl?: string }): {
  key: string;
  value: string;
}[];
