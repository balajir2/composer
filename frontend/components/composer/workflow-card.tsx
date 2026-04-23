import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Workflow = {
  id: string;
  name: string;
  description?: string | null;
  category?: string | null;
  isPublic?: boolean;
};

export function WorkflowCard({ wf, href }: { wf: Workflow; href: string }) {
  return (
    <Card className="transition-shadow hover:shadow-md">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="space-y-1">
            <CardTitle className="text-base">{wf.name}</CardTitle>
            {wf.category && <CardDescription className="text-xs">{wf.category}</CardDescription>}
          </div>
          {wf.isPublic && <Badge variant="secondary">Public</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="line-clamp-2 min-h-[2.5em] text-sm text-muted-foreground">
          {wf.description ?? "No description."}
        </p>
        <Link href={href} className={cn(buttonVariants({ size: "sm" }), "w-full")}>
          Run
        </Link>
      </CardContent>
    </Card>
  );
}
