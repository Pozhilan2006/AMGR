import { notFound } from "next/navigation";

import { FeaturePlaceholder } from "@/components/feature-placeholder";

const sections: Record<string, { title: string; description: string }> = {
  "flow-graph": {
    title: "Flow graph",
    description:
      "The graph endpoint is present but currently returns 501 Not Implemented. Member 2 graph persistence and traversal are required before graph records can be shown.",
  },
  policies: {
    title: "Policies",
    description:
      "The policy listing endpoint currently returns 501 Not Implemented. Member 3 must implement policy storage and the read-only policy listing contract.",
  },
  decisions: {
    title: "Decisions",
    description:
      "No read-only decision-history endpoint is currently exposed. Member 3 must provide a scoped decision-history API before recorded outcomes can be shown.",
  },
  approvals: {
    title: "Approvals",
    description:
      "The approval listing endpoint currently returns 501 Not Implemented. Member 3 must implement approval persistence and expose read-only request details.",
  },
  operator: {
    title: "AI Security Operator",
    description:
      "The chat endpoint exists, but requires authenticated middleware and configured provider access. The frontend does not add credentials or call an LLM directly.",
  },
  "security-operator": {
    title: "AI Security Operator",
    description:
      "The chat endpoint exists, but requires authenticated middleware and configured provider access. The frontend does not add credentials or call an LLM directly.",
  },
};

export default async function SectionPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  const feature = sections[section];

  if (!feature) notFound();

  return <FeaturePlaceholder title={feature.title} description={feature.description} />;
}