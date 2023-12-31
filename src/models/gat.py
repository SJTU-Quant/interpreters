import torch
import torch.nn as nn
import torch.nn.functional as F
import dgl.nn as dglnn
import dgl
from .graph_model import GraphModel
from .gatconv import GATConv


class GATModel(GraphModel):
    def __init__(self, d_feat=6, hidden_size=64, num_layers=2, dropout=0.0, base_model="LSTM",
                 num_graph_layer=2, heads=None, use_residual=False):
        super().__init__(d_feat=d_feat, hidden_size=hidden_size, num_layers=num_layers, dropout=dropout,
                         base_model=base_model, use_residual=use_residual, is_homogeneous=True)
        self.num_graph_layer = num_graph_layer
        self.gat_layers = nn.ModuleList()

        if not heads: # set default attention heads
            heads = [1]*num_graph_layer
        heads = [1] + heads

        for i in range(num_graph_layer-1):
            self.gat_layers.append(
                GATConv(
                    hidden_size * heads[i],
                    hidden_size,
                    heads[i+1],
                    feat_drop=0.6,
                    attn_drop=0.6,
                    activation=F.elu,
                )
            )

        self.gat_layers.append(
            GATConv(
                hidden_size * heads[-2],
                hidden_size,
                heads[-1],
                feat_drop=0.6,
                attn_drop=0.6,
                activation=None,
            )
        )
        self.reset_parameters()
        for layer in self.gat_layers:
            layer._allow_zero_in_degree = True
        self._allow_zero_in_degree = True

    def reset_parameters(self):
        gain = nn.init.calculate_gain("relu")
        nn.init.xavier_normal_(self.fc_out.weight, gain=gain)

    def get_attention(self, graph):
        h = graph.ndata['nfeat']
        attn = []
        for i, layer in enumerate(self.gat_layers):
            h, layer_attention = layer(graph, h, get_attention=True) # [E,*,H,1]
            attn.append(layer_attention)
            if i == self.num_graph_layer-1:  # last layer
                h = h.mean(1)
            else:  # other layer(s)
                h = h.flatten(1)
        return attn

    def forward_graph(self, h, index=None, return_subgraph=False, edge_weight=None):
        if index:
            subgraph = dgl.node_subgraph(self.g, index)
        else:
            subgraph = self.g
        for i, layer in enumerate(self.gat_layers):
            h = layer(subgraph, h, edge_weight)
            if i == self.num_graph_layer-1:  # last layer
                h = h.mean(1)
            else:  # other layer(s)
                h = h.flatten(1)
        if return_subgraph:
            return h, subgraph
        else: return h
