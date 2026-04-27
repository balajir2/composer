-- AlterTable
ALTER TABLE "llm_models" ADD COLUMN     "verification_message" TEXT,
ADD COLUMN     "verification_status" TEXT,
ADD COLUMN     "verified_at" TIMESTAMP(3);
