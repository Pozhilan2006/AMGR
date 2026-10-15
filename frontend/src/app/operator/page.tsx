import { PageHeader } from "@/components/layout/page-header";
import { OperatorChat } from "@/components/security-operator/OperatorChat";

export default function OperatorPage() {
  return (
    <div className="page-content">
      <PageHeader
        title="AI Security Operator"
        description="Ask the backend operator to explain decisions, inspect evidence, and draft policy actions. The frontend renders only authoritative backend responses and never invents security findings."
      />
      <OperatorChat />
    </div>
  );
}
