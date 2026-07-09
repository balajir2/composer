-- CreateTable
CREATE TABLE "workflow_assignments" (
    "id" TEXT NOT NULL,
    "workflow_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "assigned_by_id" TEXT NOT NULL,
    "assigned_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "workflow_assignments_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "workflow_assignments_workflow_id_idx" ON "workflow_assignments"("workflow_id");

-- CreateIndex
CREATE INDEX "workflow_assignments_user_id_idx" ON "workflow_assignments"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "workflow_assignments_workflow_id_user_id_key" ON "workflow_assignments"("workflow_id", "user_id");

-- AddForeignKey
ALTER TABLE "workflow_assignments" ADD CONSTRAINT "workflow_assignments_workflow_id_fkey" FOREIGN KEY ("workflow_id") REFERENCES "workflows"("id") ON DELETE CASCADE ON UPDATE CASCADE;
