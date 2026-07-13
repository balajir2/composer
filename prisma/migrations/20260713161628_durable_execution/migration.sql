-- AlterTable
ALTER TABLE "workflow_executions" ADD COLUMN     "delivery_attempts" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN     "lease_expires_at" TIMESTAMP(3),
ADD COLUMN     "lease_owner" TEXT;

-- CreateTable
CREATE TABLE "execution_events" (
    "id" TEXT NOT NULL,
    "execution_id" TEXT NOT NULL,
    "seq" INTEGER NOT NULL,
    "type" TEXT NOT NULL,
    "payload" JSONB NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "execution_events_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "rate_limit_buckets" (
    "id" TEXT NOT NULL,
    "route_key" TEXT NOT NULL,
    "client_key" TEXT NOT NULL,
    "tokens" DOUBLE PRECISION NOT NULL,
    "last_refill" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "rate_limit_buckets_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "execution_events_execution_id_idx" ON "execution_events"("execution_id");

-- CreateIndex
CREATE UNIQUE INDEX "execution_events_execution_id_seq_key" ON "execution_events"("execution_id", "seq");

-- CreateIndex
CREATE UNIQUE INDEX "rate_limit_buckets_route_key_client_key_key" ON "rate_limit_buckets"("route_key", "client_key");

