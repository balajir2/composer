-- CreateTable
CREATE TABLE "deployment_settings" (
    "key" TEXT NOT NULL,
    "value" TEXT NOT NULL,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deployment_settings_pkey" PRIMARY KEY ("key")
);
