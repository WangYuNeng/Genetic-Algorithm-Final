set -ex
python train.py --name lsgan_cifar10 \
       --dataset_mode torchvision --batch_size 32 --dataroot None \
       --model two_player_gan --gan_mode unconditional-z \
       --gpu_ids 0 \
       --download_root ./datasets/cifar10 --dataset_name CIFAR10 \
       --crop_size 32 --load_size 32 \
       --optim_type Adam \
       --d_loss_mode nsgan --g_loss_mode nsgan --which_D S \
       --netD DCGAN --netG DCGAN --ngf 128 --ndf 128 --g_norm none --d_norm batch \
       --init_type normal --init_gain 0.02 \
       --no_dropout --no_flip \
       --D_iters 1 \
       --use_pytorch_scores --score_name IS --fid_batch_size 500 \
       --print_freq 2000 --display_freq 2000 --score_freq 500 --save_giters_freq 100000
