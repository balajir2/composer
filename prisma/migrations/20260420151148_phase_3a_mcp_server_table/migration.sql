-- CreateTable
CREATE TABLE "mcp_servers" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "url" TEXT NOT NULL,
    "description" TEXT,
    "category" TEXT,
    "auth_type" TEXT NOT NULL,
    "encrypted_access_token" TEXT,
    "header_name" TEXT,
    "oauth_config" JSONB,
    "tools" JSONB,
    "connection_status" TEXT NOT NULL DEFAULT 'untested',
    "last_tested" TIMESTAMP(3),
    "last_error" TEXT,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "is_official" BOOLEAN NOT NULL DEFAULT false,
    "is_shared" BOOLEAN NOT NULL DEFAULT false,
    "headers" JSONB,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "mcp_servers_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "mcp_servers_user_id_idx" ON "mcp_servers"("user_id");

-- CreateIndex
CREATE INDEX "mcp_servers_is_shared_idx" ON "mcp_servers"("is_shared");
