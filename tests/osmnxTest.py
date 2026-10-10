import osmnx as ox

# Download the walkable street network of a city
G = ox.graph.graph_from_place("San Francisco, California, USA", network_type="walk")

# Visualize the pedestrian graph
fig, ax = ox.plot_graph(G)
