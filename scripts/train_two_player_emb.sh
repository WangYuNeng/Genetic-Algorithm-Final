source_dataset_name=$1
target_dataset_name=$2

set -ex
python train.py --name lsgan_emb \
       --dataset_mode embedding --batch_size 32 --dataroot None --max_vocab_size 200000 \
       --model two_player_gan --gan_mode unconditional \
       --gpu_ids 0 \
       --source_dataset_name $source_dataset_name --target_dataset_name $target_dataset_name \
       --d_loss_mode vanilla --g_loss_mode vanilla --which_D S \
       --lr_g 0.1 --lr_d 0.1 --beta1 0.5 --beta2 0.9 \
       --netD fc --netG fc --ngf 128 --ndf 128 --g_norm none --d_norm none \
       --init_type diagonal --init_gain 0.02 \
       --no_dropout --no_flip \
       --D_iters 5 \
       --score_name in \
       --print_freq 2000 --display_freq 2000 --score_freq 10000 \
       --save_latest_freq 100000 --save_giters_freq 100000