"""General-purpose training script for image-to-image translation.

This script works for various models (with option '--model': e.g., pix2pix, cyclegan, colorization) and
different datasets (with option '--dataset_mode': e.g., aligned, unaligned, single, colorization).
You need to specify the dataset ('--dataroot'), experiment name ('--name'), and model ('--model').

It first creates model, dataset, and visualizer given the option.
It then does standard network training. During the training, it also visualize/save the images, print/save the loss plot, and save models.
The script supports continue/resume training. Use '--continue_train' to resume your previous training.

Example:
    Train a CycleGAN model:
        python train.py --dataroot ./datasets/maps --name maps_cyclegan --model cycle_gan
    Train a pix2pix model:
        python train.py --dataroot ./datasets/facades --name facades_pix2pix --model pix2pix --direction BtoA

See options/base_options.py and options/train_options.py for more training options.
See training and test tips at: https://github.com/junyanz/pytorch-CycleGAN-and-pix2pix/blob/master/docs/tips.md
See frequently asked questions at: https://github.com/junyanz/pytorch-CycleGAN-and-pix2pix/blob/master/docs/qa.md
"""
import time
from options.train_options import TrainOptions
from data import create_dataset
from models import create_model
from util.visualizer import Visualizer
import pdb

if __name__ == '__main__':
    opt = TrainOptions().parse()   # get training options
    dataset = create_dataset(opt)  # create a dataset given opt.dataset_mode and other options
    dataset_size = len(dataset)    # get the number of images in the dataset.
    print('The number of training images = %d' % dataset_size)
    model = create_model(opt)      # create a model given opt.model and other options
    model.setup(opt)               # regular setup: load and print networks; create schedulers
    visualizer = Visualizer(opt)   # create a visualizer that display/save images and plots
    total_iters = 0                # the total number of training iterations
    g_iters = 0                # the total number of training iterations
    epoch = 0
    #for epoch in range(opt.epoch_count, opt.niter + opt.niter_decay + 1):    # outer loop for different epochs; we save the model by <epoch_count>, <epoch_count>+<save_latest_freq>
    while g_iters <  opt.total_num_giters:
        epoch_start_time = time.time()  # timer for entire epoch
        iter_data_time = time.time()    # timer for data loading per iteration
        epoch_iter = 0                  # the number of training iterations in current epoch, reset to 0 every epoch

        for i, data in enumerate(dataset):  # inner loop within one epoch
            iter_start_time = time.time()  # timer for computation per iteration

            g_iters_ = g_iters 
            if total_iters % (opt.D_iters+1) == 0:
                g_iters += 1 
            epoch_iter += 1 
            total_iters += 1 

            if g_iters % opt.print_freq == 0 and g_iters_ < g_iters:
                t_data = iter_start_time - iter_data_time
            visualizer.reset()

            model.set_input(data)          # unpack data from dataset and apply preprocessing
            model.optimize_parameters(total_iters)   # calculate loss functions, get gradients, update network weights

            if g_iters % opt.display_freq == 0 and g_iters_ < g_iters:   # display images on visdom and save images to a HTML file
                save_result = g_iters % opt.update_html_freq == 0
                model.compute_visuals()
                visualizer.display_current_results(model.get_current_visuals(), int(g_iters/opt.display_freq), opt.display_freq, save_result)

            if g_iters % opt.print_freq == 0 and g_iters_ < g_iters:    # print training losses and save logging information to the disk
                losses = model.get_current_losses()
                t_comp = (time.time() - iter_start_time) / opt.batch_size
                visualizer.print_current_losses(epoch, g_iters, losses, t_comp, t_data)
                if opt.display_id > 0:
                    visualizer.plot_current_losses(epoch, float(g_iters) / dataset_size, losses)

            if g_iters % opt.save_latest_freq == 0 and g_iters_ < g_iters:   # cache our latest model every <save_latest_freq> iterations
                print('saving the latest model (epoch %d, g_iters %d)' % (epoch, g_iters))
                save_suffix = 'iter_%d' % g_iters if opt.save_by_iter else 'latest'
                model.save_networks(save_suffix)

            iter_data_time = time.time()

            if g_iters % opt.save_giters_freq == 0 and g_iters_ < g_iters: # cache our model every <save_epoch_freq> epochs
                print('saving the model at the end of epoch %d, g_iters %d' % (epoch, g_iters))
                model.save_networks('latest')
                model.save_networks(g_iters)
        epoch += 1
        print('(epoch_%d) End of giters %d / %d \t Time Taken: %d sec' % (epoch, g_iters, opt.total_num_giters, time.time() - epoch_start_time))

        #print('End of epoch %d / %d \t Time Taken: %d sec' % (epoch, opt.niter + opt.niter_decay, time.time() - epoch_start_time))
        #model.update_learning_rate()                     # update learning rates at the end of every epoch.