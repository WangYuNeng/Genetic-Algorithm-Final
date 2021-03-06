import copy
import random

import torch

from .base_model import BaseModel
from .utils import (
    get_G_heap,
    G_Net,
    combine_mapping_networks,
    categorize_mappings,
    coshuffle,
)
from .optimizers import get_optimizer
from models.networks import networks
from models.networks.loss import GANLoss, cal_gradient_penalty
from models.networks.utils import get_prior
from util.util import one_hot


class GAGANModel(BaseModel):

    @staticmethod
    def modify_commandline_options(parser, is_train=True):
        if is_train:
            parser.add_argument(
                '--g_loss_mode',
                nargs='*',
                default=['vanilla'],
                help='lsgan | nsgan | vanilla | wgan | hinge | rsgan',
            )
            parser.add_argument(
                '--d_loss_mode',
                type=str,
                default='vanilla',
                help='lsgan | nsgan | vanilla | wgan | hinge | rsgan',
            )
            parser.add_argument('--which_D', type=str, default='S', help='Standard(S) | Relativistic_average (Ra)')

            parser.add_argument('--lambda_f', type=float, default=0.1, help='the hyper-parameter that balance Fq and Fd')
            parser.add_argument('--candi_num', type=int, default=2,
                                help='# of survived candidatures in each evolutionary iteration.')
        return parser

    def __init__(self, opt):
        BaseModel.__init__(self, opt)  # call the initialization method of BaseModel
        self.output = None
        self.loss_names = ['D_real', 'D_fake', 'D_gp', 'G', 'D']
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
            self.criterionD = GANLoss(opt.d_loss_mode, 'D', opt.which_D).to(self.device)
            # define G mutations 
            self.G_mutations = [
                GANLoss(g_loss, 'G', opt.which_D).to(self.device)
                for g_loss in opt.g_loss_mode
            ]
            # initialize optimizers
            self.optimizer_G = get_optimizer(opt.optim_type)(self.netG.parameters(), lr=opt.lr_g)
            self.optimizer_D = get_optimizer(opt.optim_type)(self.netD.parameters(), lr=opt.lr_d)
            self.optimizers.append(self.optimizer_G)
            self.optimizers.append(self.optimizer_D)

        # Evolutionary candidatures setting (init)
        self.G_candis = [copy.deepcopy(self.netG.state_dict())] * opt.candi_num
        self.optG_candis = [copy.deepcopy(self.optimizer_G.state_dict())] * opt.candi_num

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

    def backward_G(self, gen_data, criterion) -> dict:
        # pass D
        real_out = self.netD(self.inputs)
        fake_out = self.netD(gen_data)

        loss_G_fake, loss_G_real = criterion(fake_out, real_out)
        if self.opt.dataset_mode == 'embedding' and not self.opt.exact_orthogonal:
            embedding_dim = gen_data['data'].shape[1]
            weight = self.netG.module.layer.data
            loss_G_orthogonal = 0.001 / 2 * (
                (weight.T @ weight) - torch.eye(embedding_dim, device=self.device)
            ).norm()
        else:
            loss_G_orthogonal = 0.
        loss_G = loss_G_fake + loss_G_real + loss_G_orthogonal
        loss_G.backward()

        return {
            '': loss_G,
            'fake': loss_G_fake,
            'real': loss_G_real,
            'orthogonal': loss_G_orthogonal,
            'mode': criterion.loss_mode,
        }

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
            self.G_candis, self.opt_G_candis, self.loss_G = self.Evo_G(self.G_candis, self.optG_candis)
            self.G_candis, self.opt_G_candis, xo_success_rate = self.crossover(self.G_candis, self.optG_candis)
            self.loss_G = {'xo_success_rate': xo_success_rate, **self.loss_G}
        else:
            gen_data = self.forward()
            self.set_requires_grad(self.netD, True)
            self.optimizer_D.zero_grad()
            self.backward_D(gen_data)
            self.optimizer_D.step()

        self.step += 1

    def Evo_G(self, G_candis, optG_candis):
        """
        Enumerate candi_num*G_mutations to find the top
        candi_num network for fitness_score, self.netG will
        be updated using the best network.
        """

        G_heap = get_G_heap(self.opt.candi_num)

        # variation-evaluation-selection
        for G_candi, optG_candi in zip(G_candis, optG_candis):
            for criterionG in self.G_mutations:
                # Variation 
                self.netG.load_state_dict(G_candi)
                self.optimizer_G.load_state_dict(optG_candi)
                self.optimizer_G.zero_grad()
                gen_data = self.forward()
                G_losses = self.backward_G(gen_data, criterionG)
                self.optimizer_G.step()

                # Evaluation
                fitness = self.fitness_score()

                # Selection
                if fitness > G_heap.top().fitness:
                    netG_dict = copy.deepcopy(self.netG.state_dict())
                    optimizerG_dict = copy.deepcopy(self.optimizer_G.state_dict())
                    G_heap.replace(
                        G_Net(fitness=fitness, G_candis=netG_dict, optG_candis=optimizerG_dict, losses=G_losses)
                    )

        G_candis = [net.G_candis for net in G_heap.array]
        optG_candis = [net.optG_candis for net in G_heap.array]

        max_idx = G_heap.argmax()

        self.netG.load_state_dict(G_candis[max_idx])
        # self.optimizer_G.load_state_dict(optG_candis[max_idx])  # not sure if loading is necessary
        loss_G = G_heap.array[max_idx].losses
        return G_candis, optG_candis, loss_G

    def crossover(self, G_candis: list, optG_candis: list):
        """
        crossover nets
        """
        G_candis, optG_candis = coshuffle(G_candis, optG_candis)
        G_heap = get_G_heap(self.opt.candi_num)

        for G_candi, optG_candi in zip(G_candis, optG_candis):
            self.netG.load_state_dict(G_candi)
            fitness = self.fitness_score()
            if fitness > G_heap.top().fitness:
                G_heap.replace(
                    G_Net(fitness=fitness, G_candis=G_candi, optG_candis=optG_candi, losses=None)
                )
        SO_mappings, non_SO_mappings, SO_optG, non_SO_optG = categorize_mappings(G_candis, optG_candis)

        xo_total_count, xo_success_count = 0, 0
        for networks, optimizers, is_SO in zip(
            [SO_mappings, non_SO_mappings],
            [SO_optG, non_SO_optG],
            [True, False]
        ):
            for (G_candi_1, G_candi_2), optGs in zip(
                zip(networks[::2], networks[1::2]),
                zip(optimizers[::2], optimizers[1::2]),
            ):
                G_child = combine_mapping_networks(G_candi_1, G_candi_2, is_SO=is_SO)
                optG_child = random.choice(optGs)
                self.netG.load_state_dict(G_child)
                fitness = self.fitness_score()
                if fitness > G_heap.top().fitness:
                    G_heap.replace(
                        G_Net(fitness=fitness, G_candis=G_child, optG_candis=optG_child, losses=None)
                    )
                    xo_success_count += 1
                xo_total_count += 1

        G_candis = [net.G_candis for net in G_heap.array]
        optG_candis = [net.optG_candis for net in G_heap.array]
        max_idx = G_heap.argmax()
        self.netG.load_state_dict(G_candis[max_idx])
        self.optimizer_G.load_state_dict(optG_candis[max_idx])  # not sure if loading is necessary
        return G_candis, optG_candis, xo_success_count / xo_total_count

    def fitness_score(self):
        """
        Evaluate netG based on netD
        """
        with torch.no_grad():
            eval_data = self.forward()
        eval_fake = self.netD(eval_data)

        # Quality fitness score
        Fq = eval_fake.data.mean().item()
        return Fq
