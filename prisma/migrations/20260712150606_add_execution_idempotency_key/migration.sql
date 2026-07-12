-- AlterTable
ALTER TABLE "workflow_executions" ADD COLUMN     "idempotency_key" TEXT;

-- CreateIndex
CREATE UNIQUE INDEX "workflow_executions_workflow_id_idempotency_key_key" ON "workflow_executions"("workflow_id", "idempotency_key");

