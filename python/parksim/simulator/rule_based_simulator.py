import time
from typing import Dict, List

from dlp.dataset import Dataset
from dlp.visualizer import Visualizer as DlpVisualizer

from pathlib import Path

import pickle

import numpy as np
from parksim.pytypes import VehicleState

from parksim.vehicle_types import VehicleBody, VehicleConfig, VehicleTask
from parksim.route_planner.graph import WaypointsGraph
from parksim.visualizer.realtime_visualizer import RealtimeVisualizer

from parksim.agents.rule_based_stanley_vehicle import RuleBasedStanleyVehicle

np.random.seed(39) # ones with interesting cases: 20, 33, 44, 60

# These parameters should all become ROS param for simulator and vehicle
spots_data_path = '/ParkSim/data/spots_data.pickle'
offline_maneuver_path = '/ParkSim/data/parking_maneuvers.pickle'
waypoints_graph_path = '/ParkSim/data/waypoints_graph.pickle'
intent_model_path = '/ParkSim/data/smallRegularizedCNN_L0.068_01-29-2022_19-50-35.pth'
entrance_coords = [14.38, 76.21]
block_spots = [43, 44, 45]

overshoot_ranges = {'pointed_right': [(42, 48), (67, 69), (92, 94), (113, 115), (134, 136), (159, 161), (184, 186), (205, 207), (226, 228), (251, 253), (276, 278), (297, 299), (318, 320), (343, 345)],
                    'pointed_left': [(64, 66), (89, 91), (156, 158), (181, 183), (248, 250), (273, 275), (340, 342)]}

north_spot_idx_ranges = [(0, 41), (67, 91), (113, 133), (159, 183), (205, 225), (251, 275), (297, 317)]
spot_y_offset = 5

class RuleBasedSimulator(object):
    def __init__(self, dataset: Dataset, vis: RealtimeVisualizer):
        self.dlpvis = DlpVisualizer(dataset)

        self.vis = vis

        self.parking_spaces, self.occupied = self._gen_occupancy()

        for idx in block_spots:
            self.occupied[idx] = True

        self.graph = WaypointsGraph()
        self.graph.setup_with_vis(self.dlpvis)

        # Save data to offline files
        # with open('waypoints_graph.pickle', 'wb') as f:
        #     data_to_save = {'graph': self.graph, 
        #                     'entrance_coords': entrance_coords}
        #     pickle.dump(data_to_save, f)

        # with open('spots_data.pickle', 'wb') as f:
        #     data_to_save = {'parking_spaces': self.parking_spaces, 
        #                     'overshoot_ranges': overshoot_ranges, 
        #                     'north_spot_idx_ranges': north_spot_idx_ranges,
        #                     'spot_y_offset': spot_y_offset}
        #     pickle.dump(data_to_save, f)

        # spawn stuff
        
        spawn_interval_mean = 5 # Mean time for exp distribution
        spawn_interval_min = 2 # Min time for each spawn

        spawn_entering = 3 # number of vehicles to enter
        spawn_exiting = 3 # number of vehicles to exit

        self.spawn_entering_time = sorted(np.random.exponential(spawn_interval_mean, spawn_entering))
        for i in range(spawn_entering):
            self.spawn_entering_time[i] += i * spawn_interval_min

        self.spawn_exiting_time = sorted(np.random.exponential(spawn_interval_mean, spawn_exiting))

        self.num_vehicles = 0
        self.vehicles: List[RuleBasedStanleyVehicle] = []

        self.max_simulation_time = 150

        self.time = 0.0
        self.loops = 0

        # crash detection
        self.did_crash = False
        self.crash_polytopes = None

    def _gen_occupancy(self):

        # Spot guide (note: NOT VERTICES) — the i in parking_spaces[i]
        # 0-41 are top row
        # 42-66 are left second row top, 67-91 are left second row bottom
        # 92-112 are right second row top, 113-133 are right second row bottom
        # 134-158 are left third row top, 159-183 are left third row bottom
        # 184-204 are right third row top, 205-225 are right third row bottom
        # 226-250 are left fourth row top, 251-275 are left fourth row bottom
        # 276-296 are right fourth row top, 297-317 are right fourth row bottom
        # 318-342 are left fifth row, 343-363 are right fifth row

        # get parking spaces
        arr = self.dlpvis.parking_spaces.to_numpy()
        # array of tuples of x-y coords of centers of spots
        parking_spaces = np.array([[round((arr[i][2] + arr[i][4]) / 2, 3), round((arr[i][3] + arr[i][9]) / 2, 3)] for i in range(len(arr))])

        scene = self.dlpvis.dataset.get('scene', self.dlpvis.dataset.list_scenes()[0])

        # figure out which parking spaces are occupied
        car_coords = [self.dlpvis.dataset.get('obstacle', o)['coords'] for o in scene['obstacles']]
        # 1D array of booleans — are the centers of any of the cars contained within this spot's boundaries?
        occupied = np.array([any([c[0] > arr[i][2] and c[0] < arr[i][4] and c[1] < arr[i][3] and c[1] > arr[i][9] for c in car_coords]) for i in range(len(arr))])

        return parking_spaces, occupied

    # goes to an anchor point
    # convention: if entering, spot_index is positive, and if exiting, it's negative
    def add_vehicle(self, spot_index: int, vehicle_body: VehicleBody=VehicleBody(), vehicle_config: VehicleConfig=VehicleConfig()):
        # Start vehicle indexing from 1
        self.num_vehicles += 1

        # NOTE: These lines are here for now. In the ROS implementation, they will all be in the vehicle node, no the simulator node
        vehicle = RuleBasedStanleyVehicle(vehicle_id=self.num_vehicles, vehicle_body=vehicle_body, vehicle_config=vehicle_config)
        vehicle.load_parking_spaces(spots_data_path=spots_data_path)
        vehicle.load_graph(waypoints_graph_path=waypoints_graph_path)
        vehicle.load_maneuver(offline_maneuver_path=offline_maneuver_path)
        # vehicle.load_intent_model(model_path=intent_model_path)

        task_profile = []
        if spot_index > 0:
            cruise_task = VehicleTask(
                name="CRUISE", v_cruise=5, target_spot_index=spot_index)
            park_task = VehicleTask(name="PARK", target_spot_index=spot_index)
            task_profile = [cruise_task, park_task]

            state = VehicleState()
            state.x.x = entrance_coords[0] - vehicle_config.offset
            state.x.y = entrance_coords[1]
            state.e.psi = - np.pi/2

            vehicle.set_vehicle_state(state=state)
            vehicle.set_task_profile(task_profile=task_profile)
        else:
            unpark_task = VehicleTask(name="UNPARK")
            cruise_task = VehicleTask(
                name="CRUISE", v_cruise=5, target_coords=np.array(entrance_coords))
            task_profile = [unpark_task, cruise_task]

            vehicle.set_vehicle_state(spot_index=abs(spot_index))
            vehicle.set_task_profile(task_profile)

        vehicle.execute_next_task()

        self.vehicles.append(vehicle)
    

    def run(self):
        # while not run out of time and we have not reached the last waypoint yet
        while self.max_simulation_time >= self.time:

            if not self.vis.is_running():
                self.vis.render()
                continue

            # clear visualizer
            self.vis.clear_frame()

            
            # spawn vehicles
            if self.spawn_entering_time and self.time > self.spawn_entering_time[0]:
                empty_spots = [i for i in range(len(self.occupied)) if not self.occupied[i]]
                chosen_spot = np.random.choice(empty_spots)
                self.add_vehicle(chosen_spot)
                self.occupied[chosen_spot] = True
                self.spawn_entering_time.pop(0)
            
            if self.spawn_exiting_time and self.time > self.spawn_exiting_time[0]:
                empty_spots = [i for i in range(len(self.occupied)) if not self.occupied[i]]
                chosen_spot = np.random.choice(empty_spots)
                self.add_vehicle(-1 * chosen_spot)
                self.occupied[chosen_spot] = True
                self.spawn_exiting_time.pop(0)

            active_vehicles: Dict[int, RuleBasedStanleyVehicle] = {}
            for vehicle in self.vehicles:
                if not vehicle.is_all_done():
                    active_vehicles[vehicle.vehicle_id] = vehicle

            if not self.spawn_entering_time and not self.spawn_exiting_time and not active_vehicles:
                print("No Active Vehicles")
                break

            # ========== For real-time prediction only
            # add vehicle states to history
            # current_frame_states = []
            # for vehicle in self.vehicles:
            #     current_state_dict = vehicle.get_state_dict()
            #     current_frame_states.append(current_state_dict)
            # self.history.append(current_frame_states)
                
            # intent_pred_results = []
            # ===========

            for vehicle_id in active_vehicles:
                vehicle = active_vehicles[vehicle_id]

                vehicle.get_other_info(active_vehicles)
                vehicle.get_central_occupancy(self.occupied)
                vehicle.set_method_to_change_central_occupancy(self.occupied)

                vehicle.solve(time=self.time)
                # ========== For real-time prediction only
                # result = vehicle.predict_intent()
                # intent_pred_results.append(result)
                # ===========
            
            self.loops += 1
            self.time += 0.1

            # Visualize
            for vehicle in self.vehicles:

                if vehicle.is_all_done():
                    fill = (0, 0, 0, 255)
                elif vehicle.is_braking:
                    fill = (255, 0, 0, 255)
                elif vehicle.current_task in ["PARK", "UNPARK"]:
                    fill = (255, 128, 0, 255)
                else:
                    fill = (0, 255, 0, 255)

                self.vis.draw_vehicle(state=vehicle.state, fill=fill)
                # self.vis.draw_line(points=np.array([vehicle.x_ref, vehicle.y_ref]).T, color=(39,228,245, 193))
                on_vehicle_text =  str(vehicle.vehicle_id) + ":"
                on_vehicle_text += "N" if vehicle.priority is None else str(round(vehicle.priority, 3))
                # self.vis.draw_text([vehicle.state.x.x - 2, vehicle.state.x.y + 2], on_vehicle_text, size=25)
                
            # ========== For real-time prediction only
            # likelihood_radius = 15
            # for result in intent_pred_results:
            #     distribution = result.distribution
            #     for i in range(len(distribution) - 1):
            #         coords = result.all_spot_centers[i]
            #         prob = format(distribution[i], '.2f')
            #         self.vis.draw_circle(center=coords, radius=likelihood_radius*distribution[i], color=(255,65,255,255))
            #         self.vis.draw_text([coords[0]-2, coords[1]], prob, 15)
            # ===========
    
            self.vis.render()

def main():
    # Load dataset
    ds = Dataset()

    home_path = str(Path.home())
    print('Loading dataset...')
    ds.load(home_path + '/dlp-dataset/data/DJI_0012')
    print("Dataset loaded.")

    vis = RealtimeVisualizer(ds, VehicleBody())

    simulator = RuleBasedSimulator(dataset=ds, vis=vis)

    simulator.run()



if __name__ == "__main__":
    main()