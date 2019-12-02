"""Model class template

This module provides a template for users to implement custom models.
You can specify '--model template' to use this model.
The class name should be consistent with both the filename and its model option.
The filename should be <model>_dataset.py
The class name should be <Model>Dataset.py
It implements a simple image-to-image translation baseline based on regression loss.
Given input-output pairs (data_A, data_B), it learns a network netG that can minimize the following L1 loss:
    min_<netG> ||netG(data_A) - data_B||_1
You need to implement the following functions:
    <modify_commandline_options>:　Add model-specific options and rewrite default values for existing options.
    <__init__>: Initialize this model class.
    <set_input>: Unpack input data and perform data pre-processing.
    <forward>: Run forward pass. This will be called by both <optimize_parameters> and <test>.
    <optimize_parameters>: Update network weights; it will be called in every training iteration.
"""
import torch
from .base_model import BaseModel
from models.networks import networks
from models.networks.loss import GANLoss, cal_gradient_penalty
from models.networks.utils import get_prior
from util.util import one_hot

import copy 
import math 
import heapq


class EGANModel(BaseModel):

    @staticmethod
    def modify_commandline_options(parser, is_train=True):
        """Add new model-specific options and rewrite default values for existing options.

        Parameters:
            parser -- the option parser
            is_train -- if it is training phase or test phase. You can use this flag to add training-specific or test-specific options.

        Returns:
            the modified parser.
        """
        if is_train:
            parser.add_argument(
                '--g_loss_mode',
                nargs='*',
                default=['nsgan', 'lsgan', 'vanilla'],
                help='lsgan | nsgan | vanilla | wgan | hinge | rsgan',
            )
            parser.add_argument(
                '--d_loss_mode',
                type=str,
                default='lsgan',
                help='lsgan | nsgan | vanilla | wgan | hinge | rsgan',
            )
            parser.add_argument('--which_D', type=str, default='S', help='Standard(S) | Relativistic_average (Ra)') 

            parser.add_argument('--lambda_f', type=float, default=0.1, help='the hyperparameter that balance Fq and Fd')
            parser.add_argument('--candi_num', type=int, default=2, help='# of survived candidatures in each evolutinary iteration.')
            parser.add_argument('--eval_size', type=int, default=64, help='batch size during each evaluation.')
        return parser

    def __init__(self, opt):
        """Initialize this model class.

        Parameters:
            opt -- training/test options

        A few things can be done here.
        - (required) call the initialization function of BaseModel
        - define loss function, visualization images, model names, and optimizers
        """
        BaseModel.__init__(self, opt)  # call the initialization method of BaseModel
        self.output = None
        self.loss_names = ['G_real', 'G_fake', 'D_real', 'D_fake', 'D_gp', 'G', 'D']
        self.visual_names = ['real_visual', 'gen_visual']

        if self.isTrain:  # only defined during training time
            self.model_names = ['G', 'D']
        else:
            self.model_names = ['G']

        # define networks 
        self.netG = networks.define_G(opt, self.gpu_ids)
        if self.isTrain:  # only defined during training time
            self.netD = networks.define_D(opt, self.gpu_ids)
            
            # define loss functions
            self.criterionG = None # Will be define by G_mutations
            self.criterionD = GANLoss(opt.d_loss_mode, 'D', opt.which_D).to(self.device)
            # define G mutations 
            self.G_mutations = []
            for g_loss in opt.g_loss_mode: 
                self.G_mutations.append(GANLoss(g_loss, 'G', opt.which_D).to(self.device))
            # initialize optimizers
            self.optimizer_G = torch.optim.Adam(self.netG.parameters(), lr=opt.lr_g, betas=(opt.beta1, opt.beta2))
            self.optimizer_D = torch.optim.Adam(self.netD.parameters(), lr=opt.lr_d, betas=(opt.beta1, opt.beta2))
            self.optimizers.append(self.optimizer_G)
            self.optimizers.append(self.optimizer_D)
        
        # Evolutionary candidatures setting (init)
        self.G_candis = [] 
        self.optG_candis = [] 
        for i in range(opt.candi_num): 
            self.G_candis.append(copy.deepcopy(self.netG.state_dict()))
            self.optG_candis.append(copy.deepcopy(self.optimizer_G.state_dict()))

        # the # of image for each evaluation
        self.eval_size = max(math.ceil((opt.batch_size * opt.D_iters) / opt.candi_num), opt.eval_size)

    def forward(self) -> dict:
        batch_size = self.opt.batch_size
        if self.opt.gan_mode == "conditional":
            z = get_prior(self.opt.batch_size, self.opt.z_dim, self.opt.z_type, self.device)
            y = self.CatDis.sample([batch_size])
            y = one_hot(y, [batch_size, self.opt.cat_num])
            gen_data = self.netG(z, y)
            self.set_output(gen_data)
            return {'data': gen_data, 'condition': y}
        elif self.opt.gan_mode == 'unconditional':
            gen_data = self.netG(self.inputs)
            self.set_output(gen_data)
            return {'data': gen_data}
        elif self.opt.gan_mode == 'unconditional-z':
            z = get_prior(self.opt.batch_size, self.opt.z_dim, self.opt.z_type, self.device)
            gen_data = self.netG(z)
            self.set_output(gen_data)
            return {'data': gen_data}
        else:
            raise ValueError(f'unsupported gan_mode {self.opt.gan_mode}')
    
    def set_output(self, x):
        self.output = x

    def get_output(self):
        return self.output

    def backward_G(self, gen_data):
        # pass D
        real_out = self.netD(self.inputs)
        fake_out = self.netD(gen_data)

        self.loss_G_fake, self.loss_G_real = self.criterionG(fake_out, real_out) 
        self.loss_G = self.loss_G_fake + self.loss_G_real
        self.loss_G.backward() 

    def backward_D(self, gen_data):
        # pass D 
        real_out = self.netD(self.inputs)
        fake_out = self.netD(gen_data)

        self.loss_D_fake, self.loss_D_real = self.criterionD(fake_out, real_out)
        if self.opt.use_gp is True:
            self.loss_D_gp = cal_gradient_penalty(
                self.netD,
                self.inputs['data'],
                gen_data['data'],
                self.device,
                type='mixed',
                constant=1.0,
                lambda_gp=10.0,
            )[0]
        else:
            self.loss_D_gp = 0.

        self.loss_D = self.loss_D_fake + self.loss_D_real + self.loss_D_gp
        self.loss_D.backward()

    def optimize_parameters(self):
        if self.step % (self.opt.D_iters + 1) == 0:
            self.set_requires_grad(self.netD, False)
            self.Evo_G()
        else:
            gen_data = self.forward()
            self.set_requires_grad(self.netD, True)
            self.optimizer_D.zero_grad()
            self.backward_D(gen_data)
            self.optimizer_D.step()

        self.step += 1

    def Evo_G(self):
        '''
        Enumerate candi_num*G_mutations to find the top 
        candi_num network for fitness_score, self.netG will
        be updated using the best network.
        '''

        G_heap = [ (-float('inf'), None) for i in range(self.opt.candi_num) ]

        # variation-evaluation-selection
        for i in range(self.opt.candi_num):
            for criterionG in self.G_mutations: 
                # Variation 
                self.criterionG = criterionG
                self.netG.load_state_dict(self.G_candis[i])
                self.optimizer_G.load_state_dict(self.optG_candis[i])
                self.optimizer_G.zero_grad()
                gen_data = self.forward() 
                self.backward_G(gen_data)
                self.optimizer_G.step()
                self.orthogonalize(self.netG)

                # Evaluation
                with torch.no_grad():
                    eval_data = self.forward()
                fitness = self.fitness_score(eval_data)

                # Selection
                if fitness > G_heap[0][0]:
                    print(fitness)
                    netG_dict = copy.deepcopy(self.netG.state_dict())
                    optmizerG_dict = copy.deepcopy(self.optimizer_G.state_dict())
                    heapq.heapreplace(G_heap, (fitness, (netG_dict, optmizerG_dict)))
        
        self.G_candis = [ obj[1][0] for obj in enumerate(G_heap) ]
        self.optG_candis = [ obj[1][1] for obj in enumerate(G_heap) ]
        
        # find argmax fitness netG
        max_val, max_idx = -float('inf'), 0
        for i, obj in enumerate(G_heap):
            if obj[0] > max_val:
                max_val, max_idx = obj[0], i

        self.netG.load_state_dict(self.G_candis[max_idx])
        self.optimizer_G.load_state_dict(self.optG_candis[max_idx]) # not sure if loading is necessary

    def fitness_score(self, eval_data):
        '''
        Evaluate netG based on netD 
        '''
        eval_fake = self.netD(eval_data)

        # Quality fitness score
        Fq = eval_fake.data.mean().cpu().numpy()

        # Diversity fitness score
        # TODO
        Fd = 0

        return Fq + self.opt.lambda_f * Fd 

    @staticmethod
    def orthogonalize(generator, beta=0.001):
        W = generator.module.layer.weight.data
        W.copy_((1 + beta) * W - beta * W.mm(W.transpose(0, 1).mm(W)))