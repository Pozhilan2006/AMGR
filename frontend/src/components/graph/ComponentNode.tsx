type ComponentNodeProps = {
  node: {
    id: string;
    label?: string | null;
    type?: string | null;
    metadata?: Record<string, unknown>;
    component?: {
      component_id?: string;
      component_type?: string;
      name?: string | null;
    } | null;
  };
  selected?: boolean;
  onSelect?: (nodeId: string) => void;
};

function displayLabel(node: ComponentNodeProps["node"]): string {
  return (
    node.label ??
    node.component?.name ??
    node.component?.component_id ??
    node.id
  );
}

export function ComponentNode({ node, selected = false, onSelect }: ComponentNodeProps) {
  const label = displayLabel(node);
  const typeName = node.type ?? node.component?.component_type ?? "unknown";

  return (
    <button
      aria-pressed={selected}
      className="secondary-button"
      onClick={() => onSelect?.(node.id)}
      style={{
        alignItems: "flex-start",
        background: selected ? "#edf6ef" : "#fff",
        borderColor: selected ? "#9bc1a8" : "#dce4df",
        display: "flex",
        flexDirection: "column",
        gap: "4px",
        justifyContent: "flex-start",
        minHeight: "auto",
        minWidth: "180px",
        padding: "12px 14px",
        textAlign: "left",
      }}
      type="button"
    >
      <span style={{ color: "#24796e", fontSize: "10px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase" }}>
        {typeName}
      </span>
      <span style={{ color: "#1a2623", fontSize: "13px", fontWeight: 650, lineHeight: 1.3 }}>
        {label}
      </span>
      {node.component?.component_id ? (
        <span style={{ color: "#687570", fontSize: "10px", overflowWrap: "anywhere" }}>
          {node.component.component_id}
        </span>
      ) : null}
    </button>
  );
}
