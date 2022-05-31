"""Defines the Agent"""
import random
import collections
import configparser
from typing import Tuple
import dueling_qn as dqn
import torch
import cv2
import numpy as np

config = configparser.ConfigParser()
config.read("config.ini")
DEVICE = config["TRAINING"]["device"]


class Agent:
    def __init__(self, environment):
        """
        Hyperparameters definition for Agent
        """
        # State size for breakout env. SS images (210, 160, 3). Used as input size in network
        self.state_size_h = environment.observation_space.shape[0]
        self.state_size_w = environment.observation_space.shape[1]
        self.state_size_c = environment.observation_space.shape[2]

        # Activation size for breakout env. Used as output size in network
        self.action_size = environment.action_space.n

        # Image pre process params
        self.target_h = 80  # Height after process
        self.target_w = 64  # Widht after process

        # Cut 20 px from top to get rid of the score table
        self.crop_dim = [
            20,
            self.state_size_h,
            0,
            self.state_size_w,
        ]

        # Trust rate to our experiences
        self.gamma = config["TRAINING"]["gamma"]  # Discount coef for future predictions
        self.alpha = config["TRAINING"]["alpha"]  # Learning Rate

        # After many experinces epsilon will be 0.05
        # So we will do less Explore more Exploit
        # Adaptive Epsilon Decay Rate
        self.epsilon = 1  # Explore or Exploit
        self.epsilon_decay = config["TRAINING"]["epsilon"]
        self.epsilon_minimum = 0.05  # Minimum for Explore

        # Deque holds replay mem.
        self.memory = collections.deque(maxlen=config["TRAINING"]["max_memory"])

        # Create two model for DDQN algorithm
        self.online_model = dqn.DuelCNN(
            h=self.target_h, w=self.target_w, output_size=self.action_size
        ).to(DEVICE)
        self.target_model = dqn.DuelCNN(
            h=self.target_h, w=self.target_w, output_size=self.action_size
        ).to(DEVICE)
        self.target_model.load_state_dict(self.online_model.state_dict())
        self.target_model.eval()

        # Adam used as optimizer
        self.optimizer = torch.optim.Adam(self.online_model.parameters(), lr=self.alpha)

    def preProcess(self, image):
        """
        Process image crop resize, grayscale and normalize the images
        """
        frame = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)  # To grayscale
        frame = frame[
            self.crop_dim[0] : self.crop_dim[1], self.crop_dim[2] : self.crop_dim[3]
        ]  # Cut 20 px from top
        frame = cv2.resize(frame, (self.target_w, self.target_h))  # Resize
        frame = frame.reshape(self.target_w, self.target_h) / 255  # Normalize

        return frame

    def act(self, state):
        """
        Get state and do action
        Two option can be selectedd if explore select random action
        if exploit ask nnet for action
        """

        act_protocol = "Explore" if random.uniform(0, 1) <= self.epsilon else "Exploit"

        if act_protocol == "Explore":
            action = random.randrange(self.action_size)
        else:
            with torch.no_grad():
                state = torch.tensor(state, dtype=torch.float, device=DEVICE).unsqueeze(
                    0
                )
                q_values = self.online_model.forward(state)  # (1, action_size)
                action = torch.argmax(
                    q_values
                ).item()  # Returns the indices of the maximum value of all elements

        return action

    def train(self) -> Tuple[float, float]:
        """
        Train neural nets with replay memory
        returns loss and max_q val predicted from online_net
        """
        # We get out minibatch and turn it to numpy array
        state, action, reward, next_state, done = zip(
            *random.sample(self.memory, config["TRAINING"]["batch"])
        )

        # Concat batches in one array
        # (np.arr, np.arr) ==> np.BIGarr
        state = np.concatenate(state)
        next_state = np.concatenate(next_state)

        # Convert them to tensors
        state = torch.tensor(state, dtype=torch.float, device=DEVICE)
        next_state = torch.tensor(next_state, dtype=torch.float, device=DEVICE)
        action = torch.tensor(action, dtype=torch.long, device=DEVICE)
        reward = torch.tensor(reward, dtype=torch.float, device=DEVICE)
        done = torch.tensor(done, dtype=torch.float, device=DEVICE)

        # Make predictions
        state_q_values = self.online_model(state)
        next_states_q_values = self.online_model(next_state)
        next_states_target_q_values = self.target_model(next_state)

        # Find selected action's q_value
        selected_q_value = state_q_values.gather(1, action.unsqueeze(1)).squeeze(1)
        # Get indice of the max value of next_states_q_values
        # Use that indice to get a q_value from next_states_target_q_values
        # We use greedy for policy So it called off-policy
        next_states_target_q_value = next_states_target_q_values.gather(
            1, next_states_q_values.max(1)[1].unsqueeze(1)
        ).squeeze(1)
        # Use Bellman function to find expected q value
        expected_q_value = reward + self.gamma * next_states_target_q_value * (1 - done)

        # Calc loss with expected_q_value and q_value
        loss = (selected_q_value - expected_q_value.detach()).pow(2).mean()

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        return loss, torch.max(state_q_values).item()

    def storeResults(self, state, action, reward, nextState, done):
        """
        Store every result to memory
        """
        self.memory.append([state[None, :], action, reward, nextState[None, :], done])

    def adaptiveEpsilon(self):
        """
        Adaptive Epsilon means every step
        we decrease the epsilon so we do less Explore
        """
        if self.epsilon > self.epsilon_minimum:
            self.epsilon *= self.epsilon_decay