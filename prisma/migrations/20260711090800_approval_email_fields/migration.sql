-- AlterTable
ALTER TABLE "approvals" ADD COLUMN     "approver_email" TEXT,
ADD COLUMN     "via_email_link" BOOLEAN NOT NULL DEFAULT false,
ALTER COLUMN "approver_user_id" DROP NOT NULL;
