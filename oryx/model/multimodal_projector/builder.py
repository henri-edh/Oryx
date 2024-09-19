import torch
import torch.nn as nn
import re

import math

class IdentityMap(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, x, *args, **kwargs):
        return x

    @property
    def config(self):
        return {"mm_projector_type": 'identity'}


class SimpleResBlock(nn.Module):
    def __init__(self, channels):
        super().__init__()
        self.pre_norm = nn.LayerNorm(channels)

        self.proj = nn.Sequential(
            nn.Linear(channels, channels),
            nn.GELU(),
            nn.Linear(channels, channels)
        )
    def forward(self, x):
        x = self.pre_norm(x)
        return x + self.proj(x)

class SimpleMlp(nn.Module):
    def __init__(self, in_channels, out_channels, twoview=False):
        super().__init__()
        self.proj = nn.Sequential(
            nn.Linear(in_channels, out_channels),
            nn.GELU(),
            nn.Linear(out_channels, out_channels)
        )

        embed_std = 1 / math.sqrt(out_channels)
        self.image_newline = nn.Parameter(
            torch.randn(out_channels) * embed_std
        )
        self.image_begin = nn.Parameter(
            torch.randn(out_channels) * embed_std
        )
        self.image_end = nn.Parameter(
            torch.randn(out_channels) * embed_std
        )
        
        if twoview:
            self.image_sep = nn.Parameter(
                torch.randn(out_channels) * embed_std
            )

    def forward(self, x, size=(16,16), x2=None, size2=(16, 16), modalities='image'):

        if modalities in ['image', 'text']:
            h, w = size
            dtype = x.dtype
            x = x.reshape(x.shape[0], h, w, -1)
            x = self.proj(x) #b,h,w, c
            b, h, w, c = x.shape
            x = torch.cat([
                x,
                self.image_newline.reshape(1, 1, 1, c).expand(b, h, 1, c).to(dtype)
            ], dim=2)
            x = x.reshape(b, -1, c)

            if x2 is not None:
                h2, w2 = size2
                x2 = x2.reshape(x2.shape[0], h2, w2, -1)
                x2 = self.proj(x2) #b,h,w, c
                b2, h2, w2, c2 = x2.shape
                x2 = torch.cat([
                    x2,
                    self.image_newline.reshape(1, 1, 1, c).expand(b, h2, 1, c).to(dtype)
                ], dim=2)
                x2 = x2.reshape(b, -1, c)
                sep = self.image_sep.reshape(1, 1, -1).expand(b, 1, c2).to(dtype)
                x = torch.cat([x, sep, x2], dim=1)
            
            assert b == 1
            assert b2 == 1 # only support batch size 1

            begin = self.image_begin.reshape(1, 1, -1).expand(b, 1, c).to(dtype)
            end = self.image_end.reshape(1, 1, -1).expand(b, 1, c).to(dtype)
            x = torch.cat([begin, x, end], dim=1)
            return x
        elif modalities in ['video', 'video_long']:
            # x2 is the true feature, ignore x
            h, w = size
            dtype = x.dtype
            x = x.reshape(x.shape[0], h, w, -1)
            x = self.proj(x).mean() * 0.0

            h2, w2 = size2
            x2 = x2.reshape(x2.shape[0], h2, w2, -1)
            x2 = self.proj(x2) + x #b, h, w, c

            b2, h2, w2, c = x2.shape
            x2 = torch.cat([
                x2,
                self.image_newline.reshape(1, 1, 1, c).expand(b2, h2, 1, c).to(dtype)
            ], dim=2)

            x2 = x2.reshape(b2, -1, c)

            sep = self.image_sep.reshape(1, 1, -1).expand(b2, 1, c).to(dtype)
            x2 = torch.cat([x2, sep], dim=1)

            x2 = x2.flatten(0, 1)

            begin = self.image_begin.reshape(1, -1).expand(1, c).to(dtype)
            end = self.image_end.reshape(1, -1).expand(1, c).to(dtype)
            x2 = torch.cat([begin, x2, end], dim=0)
            x2 = x2.unsqueeze(0)
            return x2

def build_vision_projector(config, delay_load=False, **kwargs):
    projector_type = getattr(config, 'mm_projector_type', 'linear')

    if projector_type == 'linear':
        return nn.Linear(config.mm_hidden_size, config.hidden_size)
    
    elif projector_type == 'simple_mlp_twoview':
        return SimpleMlp(config.mm_hidden_size, config.hidden_size, twoview=True)

    mlp_gelu_match = re.match(r'^mlp(\d+)x_gelu$', projector_type)
    if mlp_gelu_match:
        mlp_depth = int(mlp_gelu_match.group(1))
        modules = [nn.Linear(config.mm_hidden_size, config.hidden_size)]
        for _ in range(1, mlp_depth):
            modules.append(nn.GELU())
            modules.append(nn.Linear(config.hidden_size, config.hidden_size))
        return nn.Sequential(*modules)

    mlp_gelu_resnet_match = re.match(r'^mlp(\d+)x_res(\d+)x_gelu$', projector_type)
    if mlp_gelu_resnet_match:
        mlp_depth = int(mlp_gelu_resnet_match.group(1))
        res_depth = int(mlp_gelu_resnet_match.group(2))
        modules = [nn.Linear(config.mm_hidden_size, config.hidden_size)]
        for _ in range(1, mlp_depth):
            modules.append(nn.GELU())
            modules.append(nn.Linear(config.hidden_size, config.hidden_size))
        for _ in range(res_depth):
            modules.append(SimpleResBlock(config.hidden_size))
        return nn.Sequential(*modules)

    if projector_type == 'identity':
        return IdentityMap()

    raise ValueError(f'Unknown projector type: {projector_type}')