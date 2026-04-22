-- AlterTable
ALTER TABLE "mcp_servers" ADD COLUMN     "original_owner_email" TEXT;

-- AlterTable
ALTER TABLE "workflow_executions" ADD COLUMN     "original_owner_email" TEXT;

-- AlterTable
ALTER TABLE "workflows" ADD COLUMN     "original_owner_email" TEXT;
