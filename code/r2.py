import torch
import torch.nn as nn
import torch.optim as optim
import matplotlib.pyplot as plt
import numpy as np
from collections import deque
import random
import pygame
import math

# Advanced Transformer-based Reasoning Network with Chain-of-Thought
class DQNReasoner(nn.Module):
    def __init__(self, state_size, action_size, num_heads=4, num_layers=3):
        super(DQNReasoner, self).__init__()
        
        # Dimensions
        self.d_model = 256
        self.state_size = state_size
        self.action_size = action_size
        
        # Input embedding with positional encoding
        self.input_embedding = nn.Sequential(
            nn.Linear(state_size, self.d_model),
            nn.LayerNorm(self.d_model),
            nn.ReLU()
        )
        
        # Positional encoding
        self.pos_encoder = nn.Parameter(torch.zeros(1, 1, self.d_model))
        
        # Multi-head self-attention layers
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=self.d_model,
            nhead=num_heads,
            dim_feedforward=512,
            dropout=0.1,
            activation='gelu',
            batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        
        # Chain-of-thought reasoning modules
        self.reasoning_layers = nn.ModuleList([
            nn.Sequential(
                nn.Linear(self.d_model, self.d_model),
                nn.LayerNorm(self.d_model),
                nn.GELU(),
                nn.Dropout(0.1)
            ) for _ in range(3)
        ])
        
        # Dual stream architecture
        self.value_stream = nn.Sequential(
            nn.Linear(self.d_model, 128),
            nn.ReLU(),
            nn.Linear(128, 1)
        )
        
        self.advantage_stream = nn.Sequential(
            nn.Linear(self.d_model, 128),
            nn.ReLU(),
            nn.Linear(128, action_size)
        )
        
        # Auxiliary prediction heads for better representation learning
        self.next_state_predictor = nn.Sequential(
            nn.Linear(self.d_model, 128),
            nn.ReLU(),
            nn.Linear(128, state_size)
        )
        
        self.inverse_dynamics = nn.Sequential(
            nn.Linear(self.d_model * 2, 128),
            nn.ReLU(),
            nn.Linear(128, action_size)
        )
    def forward(self, x):
        batch_size = x.size(0)
        
        # Input embedding with positional encoding
        x = self.input_embedding(x)
        x = x + self.pos_encoder
        
        # Multi-head self-attention
        x = self.transformer(x)
        
        # Chain-of-thought reasoning
        reasoning_outputs = []
        reasoning_state = x
        for layer in self.reasoning_layers:
            reasoning_state = layer(reasoning_state) + reasoning_state
            reasoning_outputs.append(reasoning_state)
        
        # Stack reasoning outputs and calculate attention
        stacked_outputs = torch.stack(reasoning_outputs)  # [num_layers, batch_size, seq_len, d_model]
        
        # Calculate attention weights across layers
        # Mean across the feature dimension to get [num_layers, batch_size, seq_len]
        attention_logits = stacked_outputs.mean(dim=-1)
        attention_weights = torch.softmax(attention_logits, dim=0)
        
        # Apply attention weights
        x = (stacked_outputs * attention_weights.unsqueeze(-1)).sum(0)
        
        # Dueling network architecture
        value = self.value_stream(x)
        advantage = self.advantage_stream(x)
        
        # Combine value and advantage
        q_values = value + (advantage - advantage.mean(dim=1, keepdim=True))

        
        # During training, return both q_values and next_state_pred
        if self.training:
            next_state_pred = self.next_state_predictor(x)
            return q_values, next_state_pred
        
        # During evaluation, return just q_values
        return q_values

# Prioritized Experience Replay Buffer with Hindsight Experience Replay
class ReplayBuffer:
    def __init__(self, capacity=20000):
        self.buffer = deque(maxlen=capacity)
        self.priorities = deque(maxlen=capacity)
        self.alpha = 0.6  # Priority exponent
        self.beta = 0.4  # Importance sampling exponent
        self.beta_increment = 0.001
        self.epsilon = 1e-6  # Small constant to prevent zero probabilities
    
    def push(self, state, action, reward, next_state, done):
        max_priority = max(self.priorities) if self.priorities else 1.0
        self.buffer.append((state, action, reward, next_state, done))
        self.priorities.append(max_priority)
        
        # Hindsight Experience Replay - store alternative goals
        if not done:
            self.buffer.append((state, action, 1.0, next_state, True))
            self.priorities.append(max_priority)
    
    def sample(self, batch_size):
        self.beta = min(1.0, self.beta + self.beta_increment)
        
        probs = np.array(self.priorities) ** self.alpha
        probs /= probs.sum()
        
        indices = np.random.choice(len(self.buffer), batch_size, p=probs)
        samples = [self.buffer[idx] for idx in indices]
        
        # Importance sampling weights
        weights = (len(self.buffer) * probs[indices]) ** (-self.beta)
        weights /= weights.max()
        
        # Convert samples to tensors
        states = torch.stack([s for s, _, _, _, _ in samples])
        actions = torch.tensor([a for _, a, _, _, _ in samples])
        rewards = torch.tensor([r for _, _, r, _, _ in samples])
        next_states = torch.stack([ns for _, _, _, ns, _ in samples])
        dones = torch.tensor([d for _, _, _, _, d in samples])
            
        return (states, actions, rewards, next_states, dones), indices, weights

    def update_priorities(self, indices, td_errors):
        for idx, td_error in zip(indices, td_errors):
            self.priorities[idx] = abs(td_error) + self.epsilon

    def __len__(self):
        return len(self.buffer)

# Advanced 2D navigation environment with moving obstacles
class NavigationEnv:
    def __init__(self):
        self.width = 1024
        self.height = 768
        self.agent_pos = [400, 300]
        self.target_pos = [600, 400]
        self.agent_radius = 12
        self.target_radius = 15
        self.speed = 4
        
        # Target movement parameters
        self.target_speed = 2
        self.target_angle = random.random() * 2 * math.pi

        # Add obstacles with movement parameters
        self.obstacles = [
            {'pos': [300, 400], 'radius': 50, 'angle': random.random() * 2 * math.pi, 'speed': 1.5},
            {'pos': [700, 300], 'radius': 40, 'angle': random.random() * 2 * math.pi, 'speed': 1.2},
            {'pos': [500, 600], 'radius': 45, 'angle': random.random() * 2 * math.pi, 'speed': 1.8},
            {'pos': [200, 200], 'radius': 35, 'angle': random.random() * 2 * math.pi, 'speed': 1.3}
        ]

        # Initialize Pygame with better graphics
        pygame.init()
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("Advanced DQN Navigation")
        
    def reset(self):
        # Ensure agent doesn't spawn inside obstacles
        while True:
            self.agent_pos = [random.randint(50, self.width-50), 
                            random.randint(50, self.height-50)]
            if not self._check_collision():
                break
                
        # Reset target position and movement
        self.target_pos = [random.randint(50, self.width-50),
                          random.randint(50, self.height-50)]
        self.target_angle = random.random() * 2 * math.pi
        
        # Reset obstacle positions and angles
        for obstacle in self.obstacles:
            obstacle['pos'] = [random.randint(50, self.width-50),
                             random.randint(50, self.height-50)]
            obstacle['angle'] = random.random() * 2 * math.pi
        
        return self._get_state()
        
    def step(self, action):
        # Store old target position
        old_target_pos = self.target_pos.copy()
        
        # Move target
        self.target_pos[0] += math.cos(self.target_angle) * self.target_speed
        self.target_pos[1] += math.sin(self.target_angle) * self.target_speed
        
        # Bounce target off walls
        if self.target_pos[0] < self.target_radius or self.target_pos[0] > self.width - self.target_radius:
            self.target_angle = math.pi - self.target_angle
        if self.target_pos[1] < self.target_radius or self.target_pos[1] > self.height - self.target_radius:
            self.target_angle = -self.target_angle
            
        # Check target collision with obstacles and bounce
        for obstacle in self.obstacles:
            dist = math.sqrt((self.target_pos[0] - obstacle['pos'][0])**2 + 
                           (self.target_pos[1] - obstacle['pos'][1])**2)
            if dist < (self.target_radius + obstacle['radius']):
                # Calculate bounce angle
                dx = self.target_pos[0] - obstacle['pos'][0]
                dy = self.target_pos[1] - obstacle['pos'][1]
                bounce_angle = math.atan2(dy, dx)
                
                # Move target back and apply bounce
                self.target_pos = old_target_pos
                self.target_angle = bounce_angle
                self.target_pos[0] += math.cos(self.target_angle) * self.target_speed
                self.target_pos[1] += math.sin(self.target_angle) * self.target_speed
            
        # Move obstacles and handle their collisions
        for obstacle in self.obstacles:
            # Move obstacle
            obstacle['pos'][0] += math.cos(obstacle['angle']) * obstacle['speed']
            obstacle['pos'][1] += math.sin(obstacle['angle']) * obstacle['speed']
            
            # Bounce off walls
            if obstacle['pos'][0] < obstacle['radius'] or obstacle['pos'][0] > self.width - obstacle['radius']:
                obstacle['angle'] = math.pi - obstacle['angle']
            if obstacle['pos'][1] < obstacle['radius'] or obstacle['pos'][1] > self.height - obstacle['radius']:
                obstacle['angle'] = -obstacle['angle']
                
            # Keep in bounds
            obstacle['pos'][0] = max(obstacle['radius'], min(self.width - obstacle['radius'], obstacle['pos'][0]))
            obstacle['pos'][1] = max(obstacle['radius'], min(self.height - obstacle['radius'], obstacle['pos'][1]))
            
        # Keep target in bounds
        self.target_pos[0] = max(self.target_radius, min(self.width - self.target_radius, self.target_pos[0]))
        self.target_pos[1] = max(self.target_radius, min(self.height - self.target_radius, self.target_pos[1]))
        
        # Actions: 0=up, 1=right, 2=down, 3=left, 4-7=diagonals
        old_pos = self.agent_pos.copy()

        dx = 0
        dy = 0
        if action in [0, 4, 5]:  # Up movements
            dy -= self.speed
        if action in [2, 6, 7]:  # Down movements
            dy += self.speed
        if action in [1, 5, 7]:  # Right movements
            dx += self.speed
        if action in [3, 4, 6]:  # Left movements
            dx -= self.speed

        # Normalize diagonal speed
        if dx != 0 and dy != 0:
            dx *= 0.707  # 1/√2
            dy *= 0.707

        self.agent_pos[0] += dx
        self.agent_pos[1] += dy

        # Keep agent in bounds
        self.agent_pos[0] = max(0, min(self.width, self.agent_pos[0]))
        self.agent_pos[1] = max(0, min(self.height, self.agent_pos[1]))

        # Calculate distance to target
        dist = math.sqrt((self.agent_pos[0] - self.target_pos[0])**2 + 
                       (self.agent_pos[1] - self.target_pos[1])**2)
        old_dist = math.sqrt((old_pos[0] - self.target_pos[0])**2 + 
                           (old_pos[1] - self.target_pos[1])**2)

        # Check collision with obstacles and bounce
        collision = self._check_collision()
        if collision:
            # Calculate bounce angle
            obstacle = collision
            dx = self.agent_pos[0] - obstacle['pos'][0]
            dy = self.agent_pos[1] - obstacle['pos'][1]
            bounce_angle = math.atan2(dy, dx)
            
            # Move agent back and apply bounce
            self.agent_pos = old_pos
            self.agent_pos[0] += math.cos(bounce_angle) * self.speed
            self.agent_pos[1] += math.sin(bounce_angle) * self.speed
            
            reward = -2  # Smaller penalty for bouncing
        else:
            # Calculate reward based on distance to target and movement efficiency
            reward = (old_dist - dist) * 0.5  # Reward for moving closer
            reward -= 0.1  # Small penalty for each step to encourage efficiency
        
        # Check if reached target
        done = dist < (self.agent_radius + self.target_radius)
        if done:
            reward = 200
            
        return self._get_state(), reward, done
        
    def _check_collision(self):
        for obstacle in self.obstacles:
            dist = math.sqrt((self.agent_pos[0] - obstacle['pos'][0])**2 + 
                           (self.agent_pos[1] - obstacle['pos'][1])**2)
            if dist < (self.agent_radius + obstacle['radius']):
                return obstacle
        return None
        
    def _get_state(self):
        # Enhanced state with obstacle information
        state = [
            self.agent_pos[0]/self.width,
            self.agent_pos[1]/self.height,
            self.target_pos[0]/self.width,
            self.target_pos[1]/self.height
        ]
        
        # Add distance, angle, and velocity information for obstacles
        for obstacle in self.obstacles:
            dist = math.sqrt((self.agent_pos[0] - obstacle['pos'][0])**2 + 
                           (self.agent_pos[1] - obstacle['pos'][1])**2)
            angle = math.atan2(obstacle['pos'][1] - self.agent_pos[1],
                             obstacle['pos'][0] - self.agent_pos[0])
            state.extend([
                dist/math.sqrt(self.width**2 + self.height**2),
                angle/math.pi,
                math.cos(obstacle['angle']) * obstacle['speed']/self.speed,
                math.sin(obstacle['angle']) * obstacle['speed']/self.speed
            ])
            
        return torch.tensor(state, dtype=torch.float32)
        
    def render(self):
        self.screen.fill((240, 240, 240))
        
        # Draw obstacles
        for obstacle in self.obstacles:
            pygame.draw.circle(self.screen, (100, 100, 100),
                             (int(obstacle['pos'][0]), int(obstacle['pos'][1])),
                             obstacle['radius'])
        
        # Draw target with glow effect
        for r in range(self.target_radius + 10, self.target_radius - 1, -1):
            alpha = (r - self.target_radius + 1) * 10
            s = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
            pygame.draw.circle(s, (255, 0, 0, alpha),
                             (r, r), r)
            self.screen.blit(s, (int(self.target_pos[0]-r),
                                int(self.target_pos[1]-r)))
        
        # Draw agent with trail effect
        pygame.draw.circle(self.screen, (0, 0, 255),
                         (int(self.agent_pos[0]), int(self.agent_pos[1])),
                         self.agent_radius)
                         
        pygame.display.flip()
        
    def close(self):
        pygame.quit()

# Training parameters
state_size = 20  # [agent_x, agent_y, target_x, target_y, obstacle_info...]
action_size = 8  # up, right, down, left + diagonals
batch_size = 64
gamma = 0.99
epsilon_start = 1.0
epsilon_end = 0.01
epsilon_decay = 0.997
learning_rate = 0.0005
num_episodes = 1000

# Initialize environment and DQN
env = NavigationEnv()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
dqn = DQNReasoner(state_size, action_size).to(device)
target_dqn = DQNReasoner(state_size, action_size).to(device)
target_dqn.load_state_dict(dqn.state_dict())

optimizer = optim.Adam(dqn.parameters(), lr=learning_rate)
replay_buffer = ReplayBuffer()

# Training loop
episode_rewards = []
losses = []
epsilon = epsilon_start

try:
    for episode in range(num_episodes):
        state = env.reset()
        episode_reward = 0
        done = False
        
        while not done:
            # Handle Pygame events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise KeyboardInterrupt
            
            # Epsilon-greedy action selection
            if random.random() < epsilon:
                action = random.randrange(action_size)
            else:
                with torch.no_grad():
                    q_values = dqn(state.to(device))
                    action = q_values.argmax().item()
            
            # Take step in environment
            next_state, reward, done = env.step(action)
            
            # Store transition in replay buffer
            replay_buffer.push(state, action, reward, next_state, done)
            
            # Training step
            # In your training loop
            if len(replay_buffer) >= batch_size:
                (states, actions, rewards, next_states, dones), indices, weights = replay_buffer.sample(batch_size)
                
                states = states.to(device)
                actions = actions.to(device)
                rewards = rewards.to(device)
                next_states = next_states.to(device)
                dones = dones.to(device, dtype=torch.float32)
                
                # Get Q-values (shape is [1, 64, 8] according to print output)
                q_values = dqn(states)
                
                # Reshape q_values to [batch_size, action_size]
                print(q_values)
                q_values = q_values.squeeze()  # Remove the first dimension, now shape is [64, 8]
                
                # Now gather will work correctly
                current_q_values = q_values.gather(1, actions.unsqueeze(1))
                
                # Get next Q-values
                next_q_values = target_dqn(next_states)
                next_q_values = next_q_values.squeeze(0)  # Remove first dimension
                next_q_values = next_q_values.max(1)[0].detach()
                
                # Compute target Q-values
                target_q_values = rewards + gamma * next_q_values * (1 - dones)
            
            state = next_state
            episode_reward += reward
            
            # Render environment
            env.render()
            pygame.time.wait(5)  # Slightly faster visualization
            
        # Update target network periodically
        if episode % 5 == 0:
            target_dqn.load_state_dict(dqn.state_dict())
        
        # Decay epsilon
        epsilon = max(epsilon_end, epsilon * epsilon_decay)
        
        episode_rewards.append(episode_reward)
        
        if episode % 10 == 0:
            avg_reward = np.mean(episode_rewards[-10:])
            print(f"Episode {episode}, Avg Reward: {avg_reward:.2f}, Epsilon: {epsilon:.3f}")

except KeyboardInterrupt:
    print("\nTraining interrupted")

finally:
    env.close()

# Plotting results
plt.figure(figsize=(12, 5))

plt.subplot(1, 2, 1)
plt.plot(episode_rewards)
plt.title('Episode Rewards')
plt.xlabel('Episode')
plt.ylabel('Reward')

plt.subplot(1, 2, 2)
plt.plot(losses)
plt.title('Training Loss')
plt.xlabel('Training Step')
plt.ylabel('Loss')

plt.tight_layout()
plt.show()

# Save the model
torch.save({
    'model_state_dict': dqn.state_dict(),
    'optimizer_state_dict': optimizer.state_dict(),
    'episode_rewards': episode_rewards,
    'losses': losses
}, 'dqn_reasoner_advanced.pth')
