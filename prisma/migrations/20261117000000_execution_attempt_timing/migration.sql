ALTER TABLE "workflow_executions"
ADD COLUMN "running_since" TIMESTAMP(3),
ADD COLUMN "failure_code" TEXT;
