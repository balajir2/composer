-- CreateTable
CREATE TABLE "cloud_storage_connections" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "account_email" TEXT NOT NULL,
    "encrypted_access_token" TEXT NOT NULL,
    "encrypted_refresh_token" TEXT,
    "expires_at" TIMESTAMP(3),
    "scope" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "cloud_storage_connections_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "cloud_storage_connections_user_id_idx" ON "cloud_storage_connections"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "cloud_storage_connections_user_id_provider_account_email_key" ON "cloud_storage_connections"("user_id", "provider", "account_email");
