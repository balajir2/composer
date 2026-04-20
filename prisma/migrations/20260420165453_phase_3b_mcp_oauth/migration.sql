-- CreateTable
CREATE TABLE "mcp_oauth_tokens" (
    "id" TEXT NOT NULL,
    "mcp_server_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "encrypted_access_token" TEXT NOT NULL,
    "encrypted_refresh_token" TEXT,
    "expires_at" TIMESTAMP(3),
    "scope" TEXT,
    "token_type" TEXT NOT NULL DEFAULT 'Bearer',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "mcp_oauth_tokens_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "mcp_oauth_states" (
    "id" TEXT NOT NULL,
    "mcp_server_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "state" TEXT NOT NULL,
    "code_verifier" TEXT NOT NULL,
    "redirect_uri" TEXT NOT NULL,
    "scope" TEXT,
    "expires_at" TIMESTAMP(3) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "mcp_oauth_states_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "mcp_oauth_tokens_mcp_server_id_idx" ON "mcp_oauth_tokens"("mcp_server_id");

-- CreateIndex
CREATE INDEX "mcp_oauth_tokens_user_id_idx" ON "mcp_oauth_tokens"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "mcp_oauth_tokens_mcp_server_id_user_id_key" ON "mcp_oauth_tokens"("mcp_server_id", "user_id");

-- CreateIndex
CREATE UNIQUE INDEX "mcp_oauth_states_state_key" ON "mcp_oauth_states"("state");

-- CreateIndex
CREATE INDEX "mcp_oauth_states_state_idx" ON "mcp_oauth_states"("state");

-- CreateIndex
CREATE INDEX "mcp_oauth_states_expires_at_idx" ON "mcp_oauth_states"("expires_at");
