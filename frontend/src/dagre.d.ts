declare module "dagre" {
  type GraphLabel = {
    rankdir?: "TB" | "BT" | "LR" | "RL";
    align?: "UL" | "UR" | "DL" | "DR";
    ranksep?: number;
    nodesep?: number;
    marginx?: number;
    marginy?: number;
    ranker?: "network-simplex" | "tight-tree" | "longest-path";
  };

  type NodeLabel = {
    width: number;
    height: number;
    rank?: number;
    x?: number;
    y?: number;
  };

  type EdgeLabel = {
    id?: string;
    minlen?: number;
    weight?: number;
  };

  class Graph {
    setDefaultEdgeLabel(callback: () => EdgeLabel): void;
    setGraph(label: GraphLabel): void;
    setNode(id: string, label: NodeLabel): void;
    setEdge(source: string, target: string, label?: EdgeLabel): void;
    node(id: string): NodeLabel | undefined;
  }

  const dagre: {
    graphlib: { Graph: typeof Graph };
    layout(graph: Graph): void;
  };

  export default dagre;
}
