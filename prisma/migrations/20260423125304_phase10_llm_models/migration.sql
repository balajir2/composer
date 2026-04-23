-- CreateTable
CREATE TABLE "llm_models" (
    "id" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "model_id" TEXT NOT NULL,
    "label" TEXT,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "llm_models_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "llm_models_provider_enabled_idx" ON "llm_models"("provider", "enabled");

-- CreateIndex
CREATE UNIQUE INDEX "llm_models_provider_model_id_key" ON "llm_models"("provider", "model_id");
